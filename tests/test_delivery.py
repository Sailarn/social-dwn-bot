"""The core flow: cache, download, send, record, and what happens when it fails.

This is the path every request takes, and it was the least covered code in the
project — exercised by the load harness but never asserted.
"""

import asyncio

import pytest

from fakes import FakeBot, FakeMessage, FakeNotifier, album_info, make_download, make_probe
from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
from src.core.pacing import Pacer, PlatformPacer
from src.media import fetch
from src.storage.cache import FileIdCache
from src.storage.reports import Reports
from src.storage.stats import EventLog
from src.telegram import delivery
from src.telegram.services import Services

URL = "https://instagram.com/p/ABC/"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    class Rig:
        def __init__(self):
            self.config = Config(bot_token="x", data_dir=tmp_path)
            self.cache = FileIdCache(tmp_path / "c.db", 30)
            self.events = EventLog(tmp_path / "e.db", 90, "salt")
            self.services = Services(
                cache=self.cache, events=self.events,
                chat_pacer=Pacer(0), platform_pacer=PlatformPacer(0, 60), notifier=FakeNotifier())
            self.bot = FakeBot()
            self.probe_calls: list[str] = []
            self.download_calls: list[str] = []

        def stub(self, probe_error=None, download_error=None, info=None, size=2048):
            monkeypatch.setattr(delivery, "probe", make_probe(
                error=probe_error, info=info, calls=self.probe_calls))
            monkeypatch.setattr(fetch, "download_items", make_download(
                size_bytes=size, error=download_error, calls=self.download_calls))

        async def deliver(self, url=URL, chat_id=-100, user_id=1):
            message = FakeMessage(chat_id, user_id, url)
            await delivery.deliver_clip(message, self.bot, self.config,
                                        self.services, url)
            return message

        def events_recorded(self):
            return self.events.query("SELECT * FROM events ORDER BY id")

    return Rig()


def run(coro):
    return asyncio.run(coro)


class TestHappyPath:
    def test_downloads_sends_and_caches(self, rig):
        rig.stub()
        message = run(rig.deliver())
        assert message.sends == 1
        assert rig.probe_calls == [URL] and rig.download_calls == [URL]
        # Both keys are written: the URL for an instant repeat, the clip id for
        # the same post arriving as a different link.
        assert rig.cache.get(f"url:{delivery.links.normalize_url(URL)}")
        assert rig.cache.get("Fake:ABC")

    def test_records_a_sent_event(self, rig):
        rig.stub(size=4096)
        run(rig.deliver())
        row = rig.events_recorded()[0]
        assert row["outcome"] == "sent"
        assert row["platform"] == "instagram"
        assert row["kind"] == "video"
        assert row["bytes"] == 4096
        assert row["total_ms"] >= 0
        assert row["request_id"]

    def test_an_album_is_sent_whole_but_not_cached(self, rig):
        """Each image has its own file_id, so there is nothing single to cache."""
        rig.stub(info=album_info(photos=3))
        message = run(rig.deliver())
        assert len(message.albums) == 1
        assert rig.cache.get("Instagram:album") is None


class TestCache:
    def test_url_hit_skips_the_probe_entirely(self, rig):
        """This is what makes a repeat post instant rather than merely faster."""
        rig.stub()
        run(rig.deliver())
        rig.probe_calls.clear()
        rig.download_calls.clear()

        message = run(rig.deliver())
        assert rig.probe_calls == [], "a repeat must not touch the network"
        assert rig.download_calls == []
        assert message.sends == 1
        assert rig.events_recorded()[-1]["outcome"] == "cache_hit"

    def test_clip_hit_probes_but_does_not_download(self, rig):
        """A different link for a post already sent."""
        rig.stub()
        run(rig.deliver(url="https://instagram.com/p/ABC/?igsi=1"))
        rig.download_calls.clear()

        run(rig.deliver(url="https://instagram.com/reel/ABC/"))
        assert rig.download_calls == [], "already have the file_id"


class TestFailures:
    def test_rejection_replies_and_is_counted_but_not_registered(self, rig):
        """Rejections are normal operation; the register is for bugs."""
        rig.stub(probe_error=ClipRejected("needs a login", "needs_login"))
        message = run(rig.deliver())
        assert message.replies == ["⚠️ needs a login"]
        row = rig.events_recorded()[0]
        assert (row["outcome"], row["reason"]) == ("rejected", "needs_login")
        assert Reports(rig.events).recent_errors() == []

    def test_unavailable_is_registered(self, rig):
        rig.stub(probe_error=ClipUnavailable("network problem", "network"))
        run(rig.deliver())
        assert rig.events_recorded()[0]["outcome"] == "unavailable"
        assert len(Reports(rig.events).recent_errors()) == 1

    def test_an_unexpected_error_does_not_leak_details_to_the_chat(self, rig):
        rig.stub(probe_error=RuntimeError("secret internal detail"))
        message = run(rig.deliver())
        assert message.replies == ["⚠️ something went wrong, try again"]
        assert "secret internal detail" not in message.replies[0]
        row = rig.events_recorded()[0]
        assert (row["outcome"], row["reason"]) == ("error", "unhandled")
        assert len(Reports(rig.events).recent_errors()) == 1

    def test_a_throttling_platform_is_put_in_cooldown(self, rig):
        """The protection against getting the home IP banned."""
        rig.stub(probe_error=ClipUnavailable("rate limited", "site_throttled"))
        run(rig.deliver())
        assert "instagram" in rig.services.platform_pacer._next_allowed

    def test_other_failures_do_not_trigger_a_cooldown(self, rig):
        rig.stub(probe_error=ClipUnavailable("network problem", "network"))
        run(rig.deliver())
        assert rig.services.platform_pacer._next_allowed == {}

    def test_a_download_failure_still_records_an_event(self, rig):
        rig.stub(download_error=ClipUnavailable("no file", "no_file"))
        run(rig.deliver())
        assert rig.events_recorded()[0]["outcome"] == "unavailable"


class TestPacing:
    def test_the_chat_is_paced_before_sending(self, rig):
        rig.stub()
        rig.services = Services(cache=rig.cache, events=rig.events,
                                chat_pacer=Pacer(0.05),
                                platform_pacer=PlatformPacer(0, 60), notifier=FakeNotifier())
        run(rig.deliver())
        assert -100 in rig.services.chat_pacer._next_allowed


class TestPrivacy:
    def test_identifiers_are_hashed_in_the_event_log(self, rig):
        rig.stub()
        run(rig.deliver(chat_id=-1001234567890, user_id=111111111))
        row = rig.events_recorded()[0]
        assert "1001234567890" not in str(row["chat_hash"])
        assert "111111111" not in str(row["user_hash"])


class TestAlerting:
    """A broken platform should be one message, not one per affected post."""

    def test_a_new_failure_alerts(self, rig):
        rig.stub(probe_error=ClipUnavailable("network problem", "network"))
        run(rig.deliver())
        assert len(rig.services.notifier.sent) == 1
        assert "/errors" in rig.services.notifier.sent[0]

    def test_the_same_failure_again_stays_quiet(self, rig):
        rig.stub(probe_error=ClipUnavailable("network problem", "network"))
        for _ in range(4):
            run(rig.deliver())
        assert len(rig.services.notifier.sent) == 1, "only the first occurrence"

    def test_a_rejection_never_alerts(self, rig):
        """Needing a login is normal operation, not something to wake you for."""
        rig.stub(probe_error=ClipRejected("needs a login", "needs_login"))
        run(rig.deliver())
        assert rig.services.notifier.sent == []

    def test_an_unexpected_error_alerts(self, rig):
        rig.stub(probe_error=RuntimeError("boom"))
        run(rig.deliver())
        assert len(rig.services.notifier.sent) == 1

    def test_a_successful_download_is_silent(self, rig):
        rig.stub()
        run(rig.deliver())
        assert rig.services.notifier.sent == []
