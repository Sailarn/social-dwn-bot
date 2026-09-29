"""Apify: its output mapped to our media, and what counts as failure."""

import io
import json
import urllib.error

import pytest

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
from src.core.models import MediaKind, Source
from src.media import apify

URL = "https://www.instagram.com/reel/Dd03eq4AfJC/"
REEL = {"shortCode": "Dd03eq4AfJC", "type": "Video", "caption": "a reel",
        "videoUrl": "https://scontent.cdninstagram.com/v.mp4", "videoDuration": 12.4,
        "audioUrl": "https://scontent.cdninstagram.com/a.m4a",
        "dimensionsWidth": 720, "dimensionsHeight": 1280,
        "displayUrl": "https://scontent.cdninstagram.com/cover.jpg"}


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
        assert info.key == "Instagram:Dd03eq4AfJC", "must match yt-dlp's key for the cache"
        assert info.only.kind is MediaKind.VIDEO
        assert info.only.video_url == REEL["videoUrl"]
        assert info.only.duration_seconds == 12

    def test_the_separate_audio_travels_with_the_video(self, monkeypatch, config):
        """Apify's videoUrl is Instagram's VP9 DASH stream: video only."""
        answer(monkeypatch, [REEL])
        assert apify.probe(URL, config).only.audio_url == REEL["audioUrl"]

    def test_a_long_video_is_left_to_the_cookie_route(self, monkeypatch, config):
        """Converting minutes of VP9 on 0.1 CPU would hit the timeout; cookies
        get an H.264 file directly."""
        answer(monkeypatch, [REEL | {"videoDuration": 120}])
        with pytest.raises(ClipUnavailable) as caught:
            apify.probe(URL, config)
        assert caught.value.reason == "apify_too_long"

    def test_a_carousel_keeps_videos_and_photos_in_order(self, monkeypatch, config):
        answer(monkeypatch, [{"shortCode": "C", "type": "Sidecar", "childPosts": [
            {"type": "Image", "displayUrl": "https://cdn/1.jpg"},
            {"type": "Video", "videoUrl": "https://cdn/2.mp4", "videoDuration": 5},
        ]}])
        info = apify.probe(URL, config)
        assert [item.kind for item in info.items] == [MediaKind.PHOTO, MediaKind.VIDEO]

    def test_a_carousel_without_children_uses_its_image_list(self, monkeypatch, config):
        answer(monkeypatch, [{"shortCode": "C", "type": "Sidecar",
                              "images": ["https://cdn/1.jpg", "https://cdn/2.jpg"]}])
        assert len(apify.probe(URL, config).items) == 2

    def test_a_photo_post(self, monkeypatch, config):
        answer(monkeypatch, [{"shortCode": "P", "type": "Image",
                              "displayUrl": "https://cdn/p.jpg"}])
        assert apify.probe(URL, config).only.image_url == "https://cdn/p.jpg"

    def test_an_over_long_video_is_refused(self, monkeypatch, config):
        answer(monkeypatch, [REEL | {"videoDuration": 900}])
        with pytest.raises(ClipRejected):
            apify.probe(URL, config)

    @pytest.mark.parametrize("payload", [
        [],
        [{"url": URL, "error": "not_found", "errorDescription": "Restricted profile"}],
        [{"shortCode": "X", "type": "Video"}],
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
        assert json.loads(request.data)["directUrls"] == [URL]

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
