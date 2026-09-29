"""Cookies are a fallback, not the default.

Every authenticated request spends the account's trust budget, and sustained
authenticated traffic is what gets a scraping account flagged. So: anonymous
first, cookies only for failures a session could actually fix.
"""

import asyncio
import dataclasses

import pytest
import yt_dlp

from src.core.config import Config, load_config
from src.core.errors import ClipRejected, ClipUnavailable
from src.core import retry
from src.media import extract, fetch, pipeline
from src.media.ytdlp import media_options

VIDEO_RESULT = {"id": "v", "extractor_key": "Instagram", "duration": 10,
                "formats": [{"url": "http://x/v.mp4"}]}


@pytest.fixture
def cookie_config(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    # One anonymous attempt, so call lists show the order of steps, not retries.
    return Config(bot_token="x", cookies_file=cookies, download_attempts=1)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    async def instant(seconds):
        return None
    monkeypatch.setattr(retry.asyncio, "sleep", instant)


async def _no_alert(text):
    return True


def resolve(url, config):
    """The whole pipeline, as delivery runs it."""
    return asyncio.run(pipeline.Attempt(url, config, _no_alert).resolve())


def ydl_needing_cookies(failure="Instagram sent an empty media response"):
    """Fails anonymously, succeeds once a cookiefile is supplied."""

    class Fake:
        calls = []

        def __init__(self, options):
            self.options = options
            Fake.calls.append("with_cookies" if "cookiefile" in options else "anonymous")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False, process=True):
            if "cookiefile" not in self.options:
                raise yt_dlp.utils.DownloadError(f"ERROR: {failure}")
            return VIDEO_RESULT

    Fake.calls = []
    return Fake


class TestOptions:
    def test_cookies_are_off_by_default(self, cookie_config):
        assert "cookiefile" not in media_options(cookie_config)

    def test_cookies_are_included_when_asked(self, cookie_config):
        assert "cookiefile" in media_options(cookie_config, with_cookies=True)

    def test_nothing_to_include_without_a_file(self):
        assert "cookiefile" not in media_options(Config(bot_token="x"),
                                                 with_cookies=True)


class TestFallback:
    def test_a_working_post_never_touches_the_session(self, monkeypatch, cookie_config):
        fake = ydl_needing_cookies()
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL",
                            type("Ok", (fake,), {"extract_info":
                                                 lambda self, u, download=False,
                                                 process=True: VIDEO_RESULT}))
        info = resolve("https://instagram.com/reel/X/", cookie_config)
        assert info.used_cookies is False

    def test_login_walled_post_retries_with_cookies(self, monkeypatch, cookie_config):
        fake = ydl_needing_cookies()
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        info = resolve("https://instagram.com/reel/X/", cookie_config)
        assert fake.calls == ["anonymous", "with_cookies"], "anonymous must come first"
        assert info.used_cookies is True

    def test_private_post_retries(self, monkeypatch, cookie_config):
        fake = ydl_needing_cookies("This account is private")
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        assert resolve("https://instagram.com/p/X/", cookie_config).used_cookies

    def test_age_restricted_retries(self, monkeypatch, cookie_config):
        fake = ydl_needing_cookies("This video is age-restricted")
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        assert resolve("https://instagram.com/p/X/", cookie_config).used_cookies


class TestNoPointlessRetries:
    @pytest.mark.parametrize("failure,expected", [
        ("The read operation timed out", ClipUnavailable),
        ("HTTP Error 429: Too Many Requests", ClipUnavailable),
        ("Video unavailable. It has been removed", ClipRejected),
    ])
    def test_reasons_cookies_cannot_fix_are_not_retried(
            self, monkeypatch, cookie_config, failure, expected):
        fake = ydl_needing_cookies(failure)
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        with pytest.raises(expected):
            resolve("https://instagram.com/p/X/", cookie_config)
        assert fake.calls == ["anonymous"], "must not spend a session on this"

    def test_no_retry_when_there_are_no_cookies(self, monkeypatch):
        fake = ydl_needing_cookies()
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        with pytest.raises(ClipRejected, match="needs a login"):
            resolve("https://instagram.com/p/X/", Config(bot_token="x", download_attempts=1))
        assert fake.calls == ["anonymous"]


class TestCookiesOnThrottle:
    """Opt-in, for datacenter IPs that Instagram throttles on sight."""

    THROTTLE = "HTTP Error 429: Too Many Requests"

    @pytest.fixture
    def opted_in(self, cookie_config):
        return dataclasses.replace(cookie_config, cookies_on_throttle=True)

    def test_an_instagram_throttle_retries_with_cookies(self, monkeypatch, opted_in):
        fake = ydl_needing_cookies(self.THROTTLE)
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        info = resolve("https://www.instagram.com/reel/X/", opted_in)
        assert fake.calls == ["anonymous", "with_cookies"]
        assert info.used_cookies is True

    def test_other_platforms_are_left_alone(self, monkeypatch, opted_in):
        fake = ydl_needing_cookies(self.THROTTLE)
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        with pytest.raises(ClipUnavailable):
            resolve("https://www.tiktok.com/@a/video/1", opted_in)
        assert fake.calls == ["anonymous"]

    def test_off_by_default(self, monkeypatch, cookie_config):
        fake = ydl_needing_cookies(self.THROTTLE)
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        with pytest.raises(ClipUnavailable):
            resolve("https://www.instagram.com/reel/X/", cookie_config)
        assert fake.calls == ["anonymous"]

    def test_parses_from_env(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "1:x")
        monkeypatch.setenv("COOKIES_ON_THROTTLE", "true")
        assert load_config().cookies_on_throttle is True


class TestDownloadFollowsTheProbe:
    """Media URLs from an authenticated probe are bound to that session."""

    def _capture(self, monkeypatch):
        seen = {}

        class Fake:
            def __init__(self, options):
                seen["cookiefile"] = "cookiefile" in options

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def process_ie_result(self, info, download=False):
                pass

            def download(self, urls):
                pass

        monkeypatch.setattr(fetch.yt_dlp, "YoutubeDL", Fake)
        return seen

    @pytest.mark.parametrize("used_cookies", [True, False])
    def test_download_uses_the_same_mode(self, monkeypatch, tmp_path, cookie_config,
                                         used_cookies):
        from src.core.models import Source, ClipInfo, MediaItem, MediaKind
        seen = self._capture(monkeypatch)
        (tmp_path / "0").mkdir()
        (tmp_path / "0" / "v.mp4").write_bytes(b"video")
        info = ClipInfo(key="k", title="t",
                        source=Source.COOKIES if used_cookies else Source.ANONYMOUS,
                        items=(MediaItem(kind=MediaKind.VIDEO, raw={"id": "v"}),))
        fetch.download_items("https://instagram.com/reel/X/", info, tmp_path,
                             cookie_config)
        assert seen["cookiefile"] is used_cookies
