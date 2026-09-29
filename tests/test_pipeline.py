"""The order of steps: anonymous, Apify, cookies — and when each one runs."""

import asyncio
import dataclasses

import pytest

from src.core import retry
from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
from src.core.models import ClipInfo, MediaItem, MediaKind, Source
from src.media import apify, extract, fetch, pipeline

URL = "https://www.instagram.com/reel/X/"
THROTTLED = ClipUnavailable("the site is rate-limiting us", "site_throttled")


def info(source):
    return ClipInfo(key="Instagram:X", title="t", source=source,
                    items=(MediaItem(kind=MediaKind.VIDEO, video_url="https://cdn/v.mp4"),))


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    async def instant(seconds):
        return None
    monkeypatch.setattr(retry.asyncio, "sleep", instant)


@pytest.fixture
def config(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    return Config(bot_token="x", cookies_file=cookies, apify_token="t",
                  cookies_on_throttle=True, download_attempts=1)


class Steps:
    """Stubs every step and records the order they ran in."""

    def __init__(self, monkeypatch, *, anonymous=THROTTLED, apify_result=None,
                 cookies_result=None):
        self.calls = []

        def probe(url, config, *, with_cookies=False):
            self.calls.append("cookies" if with_cookies else "anonymous")
            outcome = cookies_result if with_cookies else anonymous
            if isinstance(outcome, Exception):
                raise outcome
            return outcome or info(Source.COOKIES if with_cookies else Source.ANONYMOUS)

        def apify_probe(url, config):
            self.calls.append("apify")
            if isinstance(apify_result, Exception):
                raise apify_result
            return apify_result or info(Source.APIFY)

        monkeypatch.setattr(extract, "probe", probe)
        monkeypatch.setattr(apify, "probe", apify_probe)
        self.alerts = []

    async def alert(self, text):
        self.alerts.append(text)

    def resolve(self, config, url=URL):
        self.attempt = pipeline.Attempt(url, config, self.alert)
        return asyncio.run(self.attempt.resolve())


def test_anonymous_success_goes_no_further(monkeypatch, config):
    steps = Steps(monkeypatch, anonymous=info(Source.ANONYMOUS))
    assert steps.resolve(config).source == Source.ANONYMOUS
    assert steps.calls == ["anonymous"]


def test_apify_comes_before_the_cookie_account(monkeypatch, config):
    steps = Steps(monkeypatch)
    assert steps.resolve(config).source == Source.APIFY
    assert steps.calls == ["anonymous", "apify"]


def test_an_apify_failure_moves_on_to_cookies(monkeypatch, config):
    steps = Steps(monkeypatch,
                  apify_result=ClipUnavailable("apify found nothing", "apify_failed"))
    assert steps.resolve(config).source == Source.COOKIES
    assert steps.calls == ["anonymous", "apify", "cookies"], "apify is not retried"
    assert steps.attempt.trail == ["anonymous", "apify", "cookies"]


def test_without_a_token_it_is_anonymous_then_cookies(monkeypatch, config):
    steps = Steps(monkeypatch)
    steps.resolve(dataclasses.replace(config, apify_token=""))
    assert steps.calls == ["anonymous", "cookies"]


def test_other_platforms_never_reach_apify(monkeypatch, config):
    steps = Steps(monkeypatch, anonymous=ClipRejected("needs a login", "needs_login"))
    steps.resolve(config, url="https://x.com/u/status/1")
    assert "apify" not in steps.calls


def test_a_verdict_from_a_later_step_is_what_the_user_sees(monkeypatch, config):
    steps = Steps(monkeypatch, apify_result=ClipRejected("clip is too long", "too_long"))
    with pytest.raises(ClipRejected, match="too long"):
        steps.resolve(config)
    assert steps.calls == ["anonymous", "apify"], "a verdict ends the pipeline"


def test_a_later_breakdown_does_not_hide_the_first_failure(monkeypatch, config):
    steps = Steps(monkeypatch,
                  apify_result=ClipUnavailable("apify found nothing", "apify_failed"),
                  cookies_result=ClipUnavailable("network", "network"))
    with pytest.raises(ClipUnavailable) as caught:
        steps.resolve(config)
    assert caught.value.reason == "site_throttled"


def test_spent_credit_alerts_once(monkeypatch, config):
    steps = Steps(monkeypatch, apify_result=apify.CreditsExhausted(first_time=True))
    assert steps.resolve(config).source == Source.COOKIES
    assert len(steps.alerts) == 1
    steps = Steps(monkeypatch, apify_result=apify.CreditsExhausted(first_time=False))
    steps.resolve(config)
    assert steps.alerts == []


class TestDownload:
    def run(self, monkeypatch, config, tmp_path, fail_sources):
        calls = []

        def download_items(url, clip, destination, config):
            calls.append(clip.source)
            assert destination.is_dir(), "the disk check needs the directory to exist"
            if clip.source in fail_sources:
                raise ClipUnavailable("cdn said no", "video_fetch_failed")
            return ["file"]

        monkeypatch.setattr(fetch, "download_items", download_items)
        monkeypatch.setattr(extract, "probe", lambda url, config, *, with_cookies=False:
                            info(Source.COOKIES))
        attempt = pipeline.Attempt(URL, config, None)
        result = asyncio.run(attempt.download(info(Source.APIFY), tmp_path))
        return result, calls

    def test_apify_media_that_will_not_download_falls_back_to_cookies(
            self, monkeypatch, config, tmp_path):
        (clip, files), calls = self.run(monkeypatch, config, tmp_path, {Source.APIFY})
        assert clip.source == Source.COOKIES
        assert calls == [Source.APIFY, Source.COOKIES]

    def test_apify_media_that_downloads_stays_apify(self, monkeypatch, config, tmp_path):
        (clip, _), calls = self.run(monkeypatch, config, tmp_path, set())
        assert clip.source == Source.APIFY
        assert calls == [Source.APIFY]
