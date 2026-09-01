"""probe(): turning a post into a list of media items."""

import pytest

from conftest import download_error, fake_ydl
from src.core.errors import ClipRejected, ClipUnavailable
from src.core.limits import ALBUM_MAX_ITEMS
from src.core.models import MediaKind
from src.media import extract, twitter_photos
from src.media.ytdlp import ALLOWED_EXTRACTORS


@pytest.fixture
def patch_ydl(monkeypatch):
    def apply(**kwargs):
        monkeypatch.setattr(extract.yt_dlp, "YoutubeDL", fake_ydl(**kwargs))
    return apply


def photo_entry(identifier, url):
    return {"id": identifier, "extractor_key": "Instagram",
            "thumbnails": [{"url": url}]}


def video_entry(identifier, duration=10):
    return {"id": identifier, "extractor_key": "Instagram", "duration": duration,
            "formats": [{"url": f"http://x/{identifier}.mp4"}]}


class TestSingleItem:
    def test_real_video_result(self, patch_ydl, ytdlp_results, config):
        patch_ydl(result=ytdlp_results["instagram_video"])
        info = extract.probe("https://instagram.com/reel/X/", config)
        assert info.kind is MediaKind.VIDEO
        assert info.key == "Instagram:TESTVIDEO01"
        assert len(info.items) == 1 and info.only.is_video
        assert info.only.raw is not None, "raw is reused so download need not re-extract"

    def test_real_photo_result(self, patch_ydl, ytdlp_results, config):
        patch_ydl(result=ytdlp_results["instagram_photo"])
        info = extract.probe("https://instagram.com/p/X/", config)
        assert info.kind is MediaKind.PHOTO
        assert len(info.items) == 1
        assert info.only.image_url.startswith("http")

    def test_photo_picks_the_last_thumbnail(self, patch_ydl, config):
        """yt-dlp orders thumbnails worst to best, so the last one is full size."""
        patch_ydl(result={"id": "p", "extractor_key": "Instagram", "thumbnails": [
            {"url": "http://x/small.jpg"}, {"url": "http://x/big.jpg"}]})
        info = extract.probe("https://instagram.com/p/X/", config)
        assert info.only.image_url == "http://x/big.jpg"


class TestCarousels:
    def test_all_photos(self, patch_ydl, config):
        patch_ydl(result={"entries": [photo_entry("a", "http://x/a.jpg"),
                                      photo_entry("b", "http://x/b.jpg")]})
        info = extract.probe("https://instagram.com/p/X/", config)
        assert info.kind is MediaKind.ALBUM and info.is_album
        assert [item.image_url for item in info.items] == \
            ["http://x/a.jpg", "http://x/b.jpg"]

    def test_mixed_video_and_photos_keeps_both(self, patch_ydl, config):
        """The whole post should arrive, not just the video."""
        patch_ydl(result={"entries": [video_entry("v"),
                                      photo_entry("a", "http://x/a.jpg"),
                                      photo_entry("b", "http://x/b.jpg")]})
        info = extract.probe("https://instagram.com/p/X/", config)
        assert info.kind is MediaKind.ALBUM
        assert [item.kind for item in info.items] == \
            [MediaKind.VIDEO, MediaKind.PHOTO, MediaKind.PHOTO]
        assert info.has_video

    def test_order_is_preserved(self, patch_ydl, config):
        patch_ydl(result={"entries": [photo_entry("a", "http://x/a.jpg"),
                                      video_entry("v"),
                                      photo_entry("b", "http://x/b.jpg")]})
        info = extract.probe("https://instagram.com/p/X/", config)
        assert [item.kind for item in info.items] == \
            [MediaKind.PHOTO, MediaKind.VIDEO, MediaKind.PHOTO]

    def test_capped_at_telegrams_album_limit(self, patch_ydl, config):
        patch_ydl(result={"entries": [
            photo_entry(str(i), f"http://x/{i}.jpg") for i in range(15)]})
        info = extract.probe("https://instagram.com/p/X/", config)
        assert len(info.items) == ALBUM_MAX_ITEMS


class TestDurationGate:
    def test_single_long_video_is_rejected(self, patch_ydl, config):
        patch_ydl(result=video_entry("v", duration=400))
        with pytest.raises(ClipRejected) as caught:
            extract.probe("https://instagram.com/reel/X/", config)
        assert "5m00s" in str(caught.value)
        assert caught.value.reason == "too_long"

    def test_at_the_limit_is_allowed(self, patch_ydl, config):
        patch_ydl(result=video_entry("v", duration=300))
        assert extract.probe("https://instagram.com/reel/X/", config).only.duration_seconds == 300

    def test_missing_duration_is_treated_as_zero(self, patch_ydl, ytdlp_results, config):
        """Instagram reels report no duration; they must not be rejected."""
        assert ytdlp_results["instagram_video"].get("duration") is None
        patch_ydl(result=ytdlp_results["instagram_video"])
        assert extract.probe("https://instagram.com/reel/X/", config).only.duration_seconds == 0

    def test_a_long_video_in_a_carousel_is_dropped_not_fatal(self, patch_ydl, config):
        """The photos are still worth sending."""
        patch_ydl(result={"entries": [video_entry("v", duration=400),
                                      photo_entry("a", "http://x/a.jpg")]})
        info = extract.probe("https://instagram.com/p/X/", config)
        assert len(info.items) == 1 and not info.has_video


class TestXPhotos:
    """yt-dlp reaches the tweet but exposes no formats and no thumbnails."""

    def test_falls_back_to_x_own_endpoint(self, patch_ydl, monkeypatch, config):
        patch_ydl(result={"id": "209", "extractor_key": "twitter", "title": "t"})
        monkeypatch.setattr(twitter_photos, "photo_urls",
                            lambda url: ["http://pbs/a.jpg", "http://pbs/b.jpg"])
        info = extract.probe("https://x.com/u/status/209", config)
        assert info.kind is MediaKind.ALBUM
        assert [item.image_url for item in info.items] == \
            ["http://pbs/a.jpg", "http://pbs/b.jpg"]

    def test_still_rejects_when_x_has_nothing_either(self, patch_ydl, monkeypatch, config):
        patch_ydl(result={"id": "209", "extractor_key": "twitter"})
        monkeypatch.setattr(twitter_photos, "photo_urls", lambda url: [])
        with pytest.raises(ClipRejected, match="no video or image"):
            extract.probe("https://x.com/u/status/209", config)

    def test_the_fallback_is_not_used_for_other_platforms(self, patch_ydl, monkeypatch, config):
        patch_ydl(result={"id": "p", "extractor_key": "Instagram"})
        monkeypatch.setattr(twitter_photos, "photo_urls",
                            lambda url: pytest.fail("must not be called for instagram"))
        with pytest.raises(ClipRejected):
            extract.probe("https://instagram.com/p/X/", config)


class TestNothingUsable:
    def test_none_result_is_rejected(self, patch_ydl, config):
        patch_ydl(result=None)
        with pytest.raises(ClipRejected):
            extract.probe("https://instagram.com/p/X/", config)

    def test_login_required_is_permanent(self, patch_ydl, config):
        patch_ydl(error=download_error("ERROR: Instagram sent an empty media response."))
        with pytest.raises(ClipRejected, match="needs a login"):
            extract.probe("https://instagram.com/p/X/", config)

    def test_network_trouble_is_retryable(self, patch_ydl, config):
        patch_ydl(error=download_error("ERROR: The read operation timed out"))
        with pytest.raises(ClipUnavailable):
            extract.probe("https://instagram.com/p/X/", config)


def test_generic_extractor_is_not_allowed():
    """The SSRF control: a redirect to anything unexpected finds no extractor."""
    assert "generic" not in ALLOWED_EXTRACTORS
    assert "vm.tiktok" in ALLOWED_EXTRACTORS, "shorteners must keep working"
    assert "twitter:shortener" in ALLOWED_EXTRACTORS
