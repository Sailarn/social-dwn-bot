"""The event log, the unique-error register, and what they refuse to store."""

import sqlite3

import pytest

from src.storage.reports import Reports
from src.storage.stats import Event, EventLog, fingerprint_of
from src.storage.database import Database


@pytest.fixture
def events(tmp_path):
    return EventLog(Database.local(tmp_path / "events.db"), retention_days=90, salt="pepper")


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
        one = EventLog(Database.local(tmp_path / "a.db"), 90, salt="one")
        two = EventLog(Database.local(tmp_path / "b.db"), 90, salt="two")
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

    def test_groups_are_named_and_private_chats_are_not(self, events):
        for _ in range(3):
            events.record(Event(outcome="sent", chat_id=-1, chat_title="Friends"))
        events.record(Event(outcome="sent", chat_id=-2, chat_title="Work"))
        events.record(Event(outcome="sent", chat_id=42))
        events.record(Event(outcome="sent", chat_id=43))
        chats = Reports(events).chats(30)
        assert chats["groups"] == [("Friends", -1, 3), ("Work", -2, 1)]
        assert (chats["private_chats"], chats["private_requests"]) == (2, 2)
        assert not events.query("SELECT * FROM chats WHERE chat_id > 0"), \
            "private chats must never be stored by id"

    def test_a_renamed_group_shows_its_latest_title(self, events):
        events.record(Event(outcome="sent", chat_id=-1, chat_title="Old"))
        events.record(Event(outcome="sent", chat_id=-1, chat_title="New"))
        assert Reports(events).chats(30)["groups"] == [("New", -1, 2)]

    def test_sources_count_every_step_tried(self, events):
        events.record(Event(outcome="sent", source="anonymous", tried="anonymous",
                            total_ms=1000))
        events.record(Event(outcome="sent", source="apify", tried="anonymous,apify",
                            total_ms=30000))
        events.record(Event(outcome="sent", source="cookies",
                            tried="anonymous,apify,cookies", total_ms=40000))
        events.record(Event(outcome="cache_hit", source="cache"))
        by_step = {row["step"]: row for row in Reports(events).sources(30)}
        assert [row["step"] for row in Reports(events).sources(30)] == [
            "cache", "anonymous", "apify", "cookies"]
        assert (by_step["anonymous"]["tried"], by_step["anonymous"]["served"]) == (3, 1)
        assert (by_step["apify"]["tried"], by_step["apify"]["served"]) == (2, 1)
        assert by_step["apify"]["median_ms"] == 30000
        assert by_step["cache"]["served"] == 1

    def test_platforms_rank_by_requests(self, events):
        for outcome in ("sent", "sent", "rejected"):
            events.record(Event(outcome=outcome, platform="instagram"))
        events.record(Event(outcome="sent", platform="twitter"))
        assert Reports(events).platforms(30) == [
            {"platform": "instagram", "requests": 3, "served": 2},
            {"platform": "twitter", "requests": 1, "served": 1}]

    def test_monthly_budgets_count_attempts_not_successes(self, events):
        """A failed Apify run still costs credit; a failed cookie try still
        spends the account."""
        events.record(Event(outcome="unavailable", tried="anonymous,apify,cookies"))
        events.record(Event(outcome="sent", tried="anonymous,apify", bytes=1000))
        month = Reports(events).since(0)
        assert month == {"apify_runs": 2, "cookie_uses": 1, "sent_bytes": 1000}

    def test_empty_log_does_not_divide_by_zero(self, events):
        summary = Reports(events).summary(30)
        assert summary["total"] == 0 and summary["median_ms"] == 0


def test_unwritable_path_disables_the_log_instead_of_crashing():
    from pathlib import Path
    events = EventLog(Database.local(Path("/proc/nope/events.db")), 90, "salt")
    events.record(Event(outcome="sent"))          # must not raise
    fingerprint, is_new = events.record_error(
        platform="x", error_type="E", message="m", url="u", detail="d", request_id="r")
    assert not is_new and len(fingerprint) == 6
    assert Reports(events).summary(30)["total"] == 0


def test_an_old_event_log_gains_the_source_column(tmp_path):
    """The Pi's events.db predates `source`; CREATE IF NOT EXISTS would not add it."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, at INTEGER NOT NULL,"
                " platform TEXT, kind TEXT, outcome TEXT NOT NULL, reason TEXT,"
                " total_ms INTEGER, bytes INTEGER, reencoded INTEGER DEFAULT 0,"
                " chat_hash TEXT, user_hash TEXT, request_id TEXT)")
    old.execute("INSERT INTO events (at, outcome) VALUES (1, 'sent')")
    old.commit()
    old.close()

    log = EventLog(Database.local(path), 90, "salt")
    log.record(Event(outcome="sent", source="apify"))
    rows = log.query("SELECT outcome, source FROM events ORDER BY id")
    assert rows == [{"outcome": "sent", "source": None},
                    {"outcome": "sent", "source": "apify"}]
