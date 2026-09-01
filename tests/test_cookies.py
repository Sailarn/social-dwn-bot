"""Cookies are a fallback, not the default.

Every authenticated request spends the account's trust budget, and sustained
authenticated traffic is what gets a scraping account flagged. So: anonymous
first, cookies only for failures a session could actually fix.
"""

import pytest
import yt_dlp

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
from src.media import extract, fetch
from src.media.ytdlp import media_options

VIDEO_RESULT = {"id": "v", "extractor_key": "Instagram", "duration": 10,
                "formats": [{"url": "http://x/v.mp4"}]}


@pytest.fixture
def cookie_config(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    return Config(bot_token="x", cookies_file=cookies)


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
        info = extract.probe("https://instagram.com/reel/X/", cookie_config)
        assert info.used_cookies is False

    def test_login_walled_post_retries_with_cookies(self, monkeypatch, cookie_config):
        fake = ydl_needing_cookies()
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        info = extract.probe("https://instagram.com/reel/X/", cookie_config)
        assert fake.calls == ["anonymous", "with_cookies"], "anonymous must come first"
        assert info.used_cookies is True

    def test_private_post_retries(self, monkeypatch, cookie_config):
        fake = ydl_needing_cookies("This account is private")
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        assert extract.probe("https://instagram.com/p/X/", cookie_config).used_cookies

    def test_age_restricted_retries(self, monkeypatch, cookie_config):
        fake = ydl_needing_cookies("This video is age-restricted")
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        assert extract.probe("https://instagram.com/p/X/", cookie_config).used_cookies


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
            extract.probe("https://instagram.com/p/X/", cookie_config)
        assert fake.calls == ["anonymous"], "must not spend a session on this"

    def test_no_retry_when_there_are_no_cookies(self, monkeypatch):
        fake = ydl_needing_cookies()
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake)
        with pytest.raises(ClipRejected, match="needs a login"):
            extract.probe("https://instagram.com/p/X/", Config(bot_token="x"))
        assert fake.calls == ["anonymous"]


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
        from src.core.models import ClipInfo, MediaItem, MediaKind
        seen = self._capture(monkeypatch)
        (tmp_path / "0").mkdir()
        (tmp_path / "0" / "v.mp4").write_bytes(b"video")
        info = ClipInfo(key="k", title="t", used_cookies=used_cookies,
                        items=(MediaItem(kind=MediaKind.VIDEO, raw={"id": "v"}),))
        fetch.download_items("https://instagram.com/reel/X/", info, tmp_path,
                             cookie_config)
        assert seen["cookiefile"] is used_cookies
