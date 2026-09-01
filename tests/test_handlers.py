"""The gate: what gets through to a download, and what is refused first."""

import asyncio

import pytest

from fakes import FakeBot, FakeMessage, FakeNotifier
from src.core.config import Config
from src.core.pacing import Pacer, PlatformPacer
from src.storage.cache import FileIdCache
from src.storage.ratelimit import RateLimiter
from src.storage.stats import EventLog
from src.telegram import handlers
from src.telegram.services import Services

LINK = "https://instagram.com/p/ABC/"
ME, STRANGER, GROUP = 111111111, 999, -100


@pytest.fixture
def rig(tmp_path, monkeypatch):
    class Rig:
        def __init__(self):
            self.events = EventLog(tmp_path / "e.db", 90, "salt")
            self.services = Services(
                cache=FileIdCache(tmp_path / "c.db", 30), events=self.events,
                chat_pacer=Pacer(0), platform_pacer=PlatformPacer(0, 60), notifier=FakeNotifier())
            self.delivered: list[str] = []
            monkeypatch.setattr(handlers, "deliver_clip", self._deliver)

        async def _deliver(self, message, bot, config, services, url):
            self.delivered.append(url)

        async def handle(self, text, user_id=ME, chat_id=GROUP, config=None,
                         limit=0, caption=None):
            message = FakeMessage(chat_id, user_id, text)
            if caption is not None:
                message.text, message.caption = None, caption
            await handlers.handle_possible_link(
                message, FakeBot(), config or Config(bot_token="x"), self.services,
                asyncio.Semaphore(3), RateLimiter(limit))
            return message

    return Rig()


def run(coro):
    return asyncio.run(coro)


class TestWhatGetsThrough:
    def test_a_supported_link_is_delivered(self, rig):
        run(rig.handle(f"look at this {LINK} nice"))
        assert rig.delivered == [LINK]

    def test_a_link_in_a_caption_counts(self, rig):
        run(rig.handle("", caption=f"photo caption {LINK}"))
        assert rig.delivered == [LINK]

    @pytest.mark.parametrize("text", [
        "just talking", "", "https://youtube.com/shorts/abc", "https://vimeo.com/1",
    ])
    def test_anything_else_is_ignored_silently(self, rig, text):
        message = run(rig.handle(text))
        assert rig.delivered == []
        assert message.replies == [], "silence, not an error message"


class TestAccess:
    def test_a_stranger_is_ignored_when_an_allowlist_exists(self, rig):
        config = Config(bot_token="x", allowed_user_ids={ME})
        message = run(rig.handle(LINK, user_id=STRANGER, config=config))
        assert rig.delivered == []
        assert message.replies == [], "must not hint that the bot exists"

    def test_an_allowed_chat_lets_a_stranger_through(self, rig):
        config = Config(bot_token="x", allowed_chat_ids={GROUP})
        run(rig.handle(LINK, user_id=STRANGER, config=config))
        assert rig.delivered == [LINK]

    def test_no_lists_means_everyone(self, rig):
        run(rig.handle(LINK, user_id=STRANGER))
        assert rig.delivered == [LINK]


class TestRateLimit:
    def test_refusal_replies_and_is_counted(self, rig, monkeypatch):
        limiter = RateLimiter(1)
        limiter.allow(ME)                       # spend the only allowance

        async def handle():
            message = FakeMessage(GROUP, ME, LINK)
            await handlers.handle_possible_link(
                message, FakeBot(), Config(bot_token="x"), rig.services,
                asyncio.Semaphore(3), limiter)
            return message

        message = run(handle())
        assert rig.delivered == [], "no download work for a refused request"
        assert "rate limit" in message.replies[0]
        row = rig.events.query("SELECT * FROM events")[0]
        assert (row["outcome"], row["reason"]) == ("rate_limited", "rate_limited")
        assert row["platform"] == "instagram"


class TestPlatformPacing:
    def test_the_platform_is_paced(self, rig):
        rig.services = Services(
            cache=rig.services.cache, events=rig.events, chat_pacer=Pacer(0),
            platform_pacer=PlatformPacer(0.05, 60), notifier=FakeNotifier())
        run(rig.handle(LINK))
        assert "instagram" in rig.services.platform_pacer._next_allowed

    def test_a_cooldown_delays_the_next_request(self, rig):
        """A throttling platform must actually slow us down."""
        pacer = PlatformPacer(0, cooldown_seconds=0.08)
        pacer.penalise("instagram")
        rig.services = Services(cache=rig.services.cache, events=rig.events,
                                chat_pacer=Pacer(0), platform_pacer=pacer,
                                notifier=FakeNotifier())

        async def timed():
            import time
            started = time.monotonic()
            await rig.handle(LINK)
            return time.monotonic() - started

        assert run(timed()) >= 0.05
