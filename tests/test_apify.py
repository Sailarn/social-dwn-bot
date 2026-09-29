"""Apify: its output mapped to our media, and what counts as failure."""

import io
import json
import urllib.error
from pathlib import Path

import pytest

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
from src.core.limits import ALBUM_MAX_ITEMS
from src.core.models import MediaKind, Source
from src.media import apify

URL = "https://www.instagram.com/reel/Dd0684ghXSs/"
# Real responses from data-slayer/instagram-post-details, trimmed to the fields
# we read, URLs replaced.
POSTS = json.loads((Path(__file__).parent / "fixtures" / "apify_posts.json").read_text())
REEL = POSTS["reel"]


@pytest.fixture(autouse=True)
def fresh_month(monkeypatch):
    monkeypatch.setattr(apify, "_exhausted_until", 0.0)


@pytest.fixture
def config():
    return Config(bot_token="x", apify_token="apify_api_secret")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(monkeypatch, payload=None, status=None, body=b""):
    """Stub the HTTP call; returns the list of requests made."""
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        if status is not None:
            raise urllib.error.HTTPError(request.full_url, status, "err", {},
                                         io.BytesIO(body))
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(apify.urllib.request, "urlopen", urlopen)
    return requests


class TestMapping:
    def test_a_reel_becomes_one_video(self, monkeypatch, config):
        answer(monkeypatch, [REEL])
        info = apify.probe(URL, config)
        assert info.source == Source.APIFY
        assert info.key == "Instagram:Dd0684ghXSs", "must match yt-dlp's key for the cache"
        assert info.only.kind is MediaKind.VIDEO
        assert info.only.video_url == REEL["video_url"]
        assert info.only.audio_url is None, "the progressive file carries its audio"
        assert info.only.duration_seconds == 31

    def test_a_photo_post_takes_the_largest_image(self, monkeypatch, config):
        answer(monkeypatch, [POSTS["photo"]])
        largest = max(POSTS["photo"]["image_versions"]["items"], key=lambda i: i["width"])
        assert apify.probe(URL, config).only.image_url == largest["url"]

    def test_a_video_carousel(self, monkeypatch, config):
        answer(monkeypatch, [POSTS["video_carousel"]])
        info = apify.probe(URL, config)
        assert info.is_album
        assert all(item.kind is MediaKind.VIDEO for item in info.items)
        assert len(info.items) == ALBUM_MAX_ITEMS - 1, "9 parts, all kept"

    def test_an_image_carousel(self, monkeypatch, config):
        answer(monkeypatch, [POSTS["image_carousel"]])
        info = apify.probe(URL, config)
        assert [item.kind for item in info.items] == [MediaKind.PHOTO] * 8

    def test_an_over_long_video_is_refused(self, monkeypatch, config):
        answer(monkeypatch, [REEL | {"video_duration": 900}])
        with pytest.raises(ClipRejected):
            apify.probe(URL, config)

    def test_the_caption_becomes_the_title(self, monkeypatch, config):
        answer(monkeypatch, [REEL | {"caption": {"text": "hello"}}])
        assert apify.probe(URL, config).title == "hello"

    @pytest.mark.parametrize("payload", [
        [],
        [{"url": URL, "error": "not_found", "errorDescription": "Restricted profile"}],
        [{"code": "X", "media_type": 2}],
        [{"media_type": 2, "video_url": "https://cdn/v.mp4"}],
    ])
    def test_nothing_usable_is_a_failure_not_a_verdict(self, monkeypatch, config, payload):
        """So the pipeline moves on to cookies."""
        answer(monkeypatch, payload)
        with pytest.raises(ClipUnavailable) as caught:
            apify.probe(URL, config)
        assert caught.value.reason == "apify_failed"


class TestRequest:
    def test_the_token_goes_in_a_header_never_the_url(self, monkeypatch, config):
        requests = answer(monkeypatch, [REEL])
        apify.probe(URL, config)
        request = requests[0]
        assert "apify_api_secret" not in request.full_url
        assert request.get_header("Authorization") == "Bearer apify_api_secret"
        assert json.loads(request.data)["postUrls"] == [URL]

    def test_a_server_error_is_a_failure(self, monkeypatch, config):
        answer(monkeypatch, status=500, body=b"oops")
        with pytest.raises(ClipUnavailable) as caught:
            apify.probe(URL, config)
        assert caught.value.reason == "apify_failed"


class TestCredits:
    def test_spent_credit_is_reported_once_then_skipped(self, monkeypatch, config):
        requests = answer(monkeypatch, status=402,
                          body=b'{"error":{"type":"user-monthly-usage-limit-exceeded"}}')
        with pytest.raises(apify.CreditsExhausted) as first:
            apify.probe(URL, config)
        assert first.value.first_time is True
        with pytest.raises(apify.CreditsExhausted) as second:
            apify.probe(URL, config)
        assert second.value.first_time is False
        assert len(requests) == 1, "no calls until the month turns"

    def test_the_skip_lasts_until_the_first_of_next_month(self):
        december_15 = 1765756800  # 2025-12-15 00:00 UTC
        assert apify._start_of_next_month(december_15) == 1767225600  # 2026-01-01

    def test_only_instagram_and_only_with_a_token(self, config):
        assert apify.applies_to(URL, config)
        assert not apify.applies_to("https://x.com/u/status/1", config)
        assert not apify.applies_to(URL, Config(bot_token="x"))
