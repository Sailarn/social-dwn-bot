"""Admin command rendering.

These exist because /stats shipped broken: Telegram's Markdown reads `_` as
italic, and every failure reason is a slug like `needs_login`, so the API
rejected the whole message with "can't find end of the entity".
"""

import asyncio
import re
import time

import pytest

from fakes import FakeNotifier
from src.core.config import Config
from src.core.pacing import Pacer, PlatformPacer
from src.storage.cache import FileIdCache
from src.storage.stats import Event, EventLog
from src.telegram import admin
from src.telegram.services import Services


def services_for(events, cache=None):
    return Services(cache=cache, events=events,
                    chat_pacer=Pacer(0), platform_pacer=PlatformPacer(0, 0), notifier=FakeNotifier())

ADMIN_ID = 111111111

TAG = re.compile(r"</?([a-z]+)[^>]*>")
ALLOWED_TAGS = {"b", "i", "code", "pre", "a"}


class FakeMessage:
    def __init__(self, text="", user_id=ADMIN_ID):
        self.text = text
        self.from_user = type("User", (), {"id": user_id})()
        self.sent = None
        self.parse_mode = None

    async def reply(self, text, **kwargs):
        self.sent = text
        self.parse_mode = kwargs.get("parse_mode")


@pytest.fixture
def events(tmp_path):
    log = EventLog(tmp_path / "e.db", 90, "salt")
    for _ in range(3):
        log.record(Event(outcome="sent", platform="instagram", kind="video",
                         total_ms=2400, bytes=3_000_000, chat_id=-100, user_id=1))
    # Reasons are slugs with underscores - the exact thing that broke Markdown.
    for reason in ("needs_login", "no_media", "rate_limited"):
        log.record(Event(outcome="rejected", platform="instagram", reason=reason,
                         chat_id=-100, user_id=1))
    return log


@pytest.fixture
def admin_config():
    return Config(bot_token="x", admin_user_ids={ADMIN_ID})


def render(handler, message, **kwargs):
    asyncio.run(handler(message, **kwargs))
    return message


def assert_valid_html(text: str):
    """Tags must be known and balanced, or Telegram rejects the message."""
    stack = []
    for match in TAG.finditer(text):
        tag = match.group(1)
        assert tag in ALLOWED_TAGS, f"unsupported tag <{tag}>"
        if match.group(0).startswith("</"):
            assert stack and stack[-1] == tag, f"unbalanced </{tag}> in: {text[:120]}"
            stack.pop()
        else:
            stack.append(tag)
    assert not stack, f"unclosed tags {stack}"


class TestStats:
    def test_renders_valid_html(self, events, admin_config):
        message = render(admin.handle_stats, FakeMessage("/stats"),
                         config=admin_config, services=services_for(events))
        assert message.parse_mode == "HTML"
        assert_valid_html(message.sent)

    def test_underscored_reasons_survive(self, events, admin_config):
        """The original bug: `needs_login` opened an italic entity that never closed."""
        message = render(admin.handle_stats, FakeMessage("/stats"),
                         config=admin_config, services=services_for(events))
        assert "needs_login" in message.sent
        assert "rate_limited" in message.sent

    def test_an_empty_log_still_renders(self, tmp_path, admin_config):
        empty = EventLog(tmp_path / "empty.db", 90, "salt")
        message = render(admin.handle_stats, FakeMessage("/stats"),
                         config=admin_config, services=services_for(empty))
        assert_valid_html(message.sent)


class TestErrors:
    def test_no_errors_message(self, tmp_path, admin_config):
        empty = EventLog(tmp_path / "empty.db", 90, "salt")
        message = render(admin.handle_errors, FakeMessage("/errors"),
                         config=admin_config, services=services_for(empty))
        assert "no errors" in message.sent

    def test_listing_escapes_hostile_text(self, tmp_path, admin_config):
        """An error message containing HTML must not become markup."""
        log = EventLog(tmp_path / "e.db", 90, "salt")
        log.record_error(platform="instagram", error_type="ClipUnavailable",
                         message="<b>boom</b> & <script>x</script>",
                         url="https://x/?a=1&b=2", detail="trace <here>",
                         request_id="r1")
        message = render(admin.handle_errors, FakeMessage("/errors"),
                         config=admin_config, services=services_for(log))
        assert "<script>" not in message.sent
        assert "&lt;b&gt;boom&lt;/b&gt;" in message.sent
        assert_valid_html(message.sent)

    def test_detail_escapes_and_gives_the_grep(self, tmp_path, admin_config):
        log = EventLog(tmp_path / "e.db", 90, "salt")
        fingerprint, _ = log.record_error(
            platform="tiktok", error_type="ClipUnavailable", message="rehydration & co",
            url="https://vm.tiktok.com/Z", detail="Traceback <most recent>",
            request_id="a3f21c")
        message = render(admin.handle_errors, FakeMessage(f"/errors {fingerprint}"),
                         config=admin_config, services=services_for(log))
        assert "grep a3f21c" in message.sent
        assert "&amp;" in message.sent
        assert_valid_html(message.sent)

    def test_unknown_id_is_reported(self, tmp_path, admin_config):
        log = EventLog(tmp_path / "e.db", 90, "salt")
        message = render(admin.handle_errors, FakeMessage("/errors nosuch"),
                         config=admin_config, services=services_for(log))
        assert "no error with id" in message.sent
        assert_valid_html(message.sent)


class TestHealth:
    def test_renders(self, events, admin_config, tmp_path):
        message = render(
            admin.handle_health, FakeMessage("/health"), config=admin_config,
            services=services_for(events, FileIdCache(tmp_path / "c.db", 30)),
            download_slots=asyncio.Semaphore(2), started_at=time.time() - 100)
        assert_valid_html(message.sent)
        assert "yt-dlp" in message.sent


class TestGating:
    @pytest.mark.parametrize("handler,text", [
        (admin.handle_stats, "/stats"),
        (admin.handle_errors, "/errors"),
    ])
    def test_non_admin_gets_silence(self, events, admin_config, handler, text):
        message = render(handler, FakeMessage(text, user_id=999),
                         config=admin_config, services=services_for(events))
        assert message.sent is None
