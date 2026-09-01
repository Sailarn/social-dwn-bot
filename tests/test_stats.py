"""The event log, the unique-error register, and what they refuse to store."""

import sqlite3

import pytest

from src.storage.reports import Reports
from src.storage.stats import Event, EventLog, fingerprint_of


@pytest.fixture
def events(tmp_path):
    return EventLog(tmp_path / "events.db", retention_days=90, salt="pepper")


class TestFingerprint:
    def test_same_fault_on_different_posts_is_one_signature(self):
        """Otherwise a broken extractor becomes hundreds of rows."""
        a = fingerprint_of("instagram", "ClipUnavailable",
                           "empty media response for Cx1y2z3AbCd")
        b = fingerprint_of("instagram", "ClipUnavailable",
                           "empty media response for Dy4w5x6EfGh")
        assert a == b

    def test_urls_and_numbers_do_not_split_a_signature(self):
        a = fingerprint_of("tiktok", "ClipUnavailable",
                           "failed https://vm.tiktok.com/ZM1 after 3 tries")
        b = fingerprint_of("tiktok", "ClipUnavailable",
                           "failed https://vm.tiktok.com/ZM9 after 7 tries")
        assert a == b

    def test_different_faults_stay_separate(self):
        assert fingerprint_of("instagram", "ClipUnavailable", "empty media response") \
            != fingerprint_of("instagram", "ClipUnavailable", "connection reset")

    def test_platform_separates_the_same_message(self):
        assert fingerprint_of("instagram", "E", "boom") != \
               fingerprint_of("tiktok", "E", "boom")


class TestErrorRegister:
    def test_first_occurrence_is_new_and_keeps_detail(self, events):
        fingerprint, is_new = events.record_error(
            platform="instagram", error_type="ClipUnavailable", message="boom",
            url="https://instagram.com/p/X/", detail="traceback here",
            request_id="abc123")
        assert is_new
        row = Reports(events).error_detail(fingerprint)
        assert row["url"] == "https://instagram.com/p/X/"
        assert row["detail"] == "traceback here"
        assert row["request_id"] == "abc123"

    def test_repeat_only_increments_a_counter(self, events):
        first, _ = events.record_error(
            platform="instagram", error_type="E", message="boom for post 111",
            url="u1", detail="first detail", request_id="r1")
        second, is_new = events.record_error(
            platform="instagram", error_type="E", message="boom for post 222",
            url="u2", detail="second detail", request_id="r2")
        assert second == first and not is_new

        row = Reports(events).error_detail(first)
        assert row["seen_count"] == 2
        assert row["detail"] == "first detail", "repeats must not overwrite detail"
        assert row["url"] == "u1"


class TestPrivacy:
    def test_identifiers_are_hashed_not_stored(self, events, tmp_path):
        events.record(Event(outcome="sent", platform="instagram",
                            chat_id=-1001234567890, user_id=111111111))
        raw = sqlite3.connect(tmp_path / "events.db").execute(
            "SELECT chat_hash, user_hash FROM events").fetchone()
        assert "1001234567890" not in str(raw)
        assert "111111111" not in str(raw)
        assert len(raw[0]) == 12

    def test_the_same_chat_hashes_consistently(self, events):
        """Counting distinct groups has to work without naming them."""
        for _ in range(2):
            events.record(Event(outcome="sent", chat_id=-100))
        events.record(Event(outcome="sent", chat_id=-200))
        assert Reports(events).summary(30)["chats"] == 2

    def test_a_different_salt_gives_a_different_hash(self, tmp_path):
        one = EventLog(tmp_path / "a.db", 90, salt="one")
        two = EventLog(tmp_path / "b.db", 90, salt="two")
        assert one._hash(42) != two._hash(42)


class TestSummary:
    def test_counts_by_outcome(self, events):
        for outcome in ("sent", "sent", "cache_hit", "rejected"):
            events.record(Event(outcome=outcome, platform="instagram"))
        summary = Reports(events).summary(30)
        assert summary["total"] == 4
        assert summary["counts"]["sent"] == 2
        assert summary["counts"]["cache_hit"] == 1

    def test_failure_reasons_are_ranked(self, events):
        for reason in ("needs_login", "needs_login", "network"):
            events.record(Event(outcome="rejected", reason=reason))
        assert Reports(events).summary(30)["reasons"][0] == ("needs_login", 2)

    def test_timings_come_from_sent_events_only(self, events):
        events.record(Event(outcome="sent", total_ms=1000))
        events.record(Event(outcome="sent", total_ms=3000))
        events.record(Event(outcome="rejected", total_ms=99999))
        summary = Reports(events).summary(30)
        assert summary["median_ms"] in (1000, 3000)
        assert summary["p95_ms"] == 3000

    def test_busiest_chats_are_ordered(self, events):
        for _ in range(3):
            events.record(Event(outcome="sent", chat_id=-1))
        events.record(Event(outcome="sent", chat_id=-2))
        busiest = Reports(events).busiest_chats(30)
        assert [count for _, count in busiest] == [3, 1]

    def test_empty_log_does_not_divide_by_zero(self, events):
        summary = Reports(events).summary(30)
        assert summary["total"] == 0 and summary["median_ms"] == 0


def test_unwritable_path_disables_the_log_instead_of_crashing():
    from pathlib import Path
    events = EventLog(Path("/proc/nope/events.db"), 90, "salt")
    events.record(Event(outcome="sent"))          # must not raise
    fingerprint, is_new = events.record_error(
        platform="x", error_type="E", message="m", url="u", detail="d", request_id="r")
    assert not is_new and len(fingerprint) == 6
    assert Reports(events).summary(30)["total"] == 0
