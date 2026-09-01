"""Downloading a post's items, and reusing the probe's result."""

import io
import urllib.request

import pytest

from src.core.errors import ClipRejected, ClipUnavailable
from src.core.limits import ALBUM_TOTAL_BYTES, PHOTO_UPLOAD_LIMIT_BYTES
from src.core.models import ClipInfo, MediaItem, MediaKind
from src.media import fetch


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Image URLs pass the SSRF guard unless a test says otherwise."""
    monkeypatch.setattr("src.core.urlguard.resolved_addresses",
                        lambda host: ["93.184.216.34"])


def fake_urlopen(payload: bytes):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout=None):
        return Response(payload)

    return opener


def photo(url="https://cdn.example/a.jpg"):
    return MediaItem(kind=MediaKind.PHOTO, image_url=url)


def info_of(*items):
    return ClipInfo(key="Instagram:abc", title="t", items=tuple(items))


class TestImages:
    def test_writes_one_file_per_item(self, monkeypatch, tmp_path, config):
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(b"\xff\xd8\xffdata"))
        downloaded = fetch.download_items(
            "u", info_of(photo("https://cdn.example/a.jpg"), photo("https://cdn.example/b.jpg")),
            tmp_path, config)
        assert len(downloaded) == 2
        assert all(entry.path.read_bytes() == b"\xff\xd8\xffdata" for entry in downloaded)

    def test_items_get_separate_directories(self, monkeypatch, tmp_path, config):
        """Two items must not overwrite each other's file."""
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(b"x"))
        downloaded = fetch.download_items(
            "u", info_of(photo(), photo()), tmp_path, config)
        assert downloaded[0].path != downloaded[1].path

    def test_oversized_image_is_rejected(self, monkeypatch, tmp_path, config):
        too_big = b"x" * (PHOTO_UPLOAD_LIMIT_BYTES + 1)
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(too_big))
        with pytest.raises(ClipRejected, match="10 MB"):
            fetch.download_items("u", info_of(photo()), tmp_path, config)

    def test_fetch_failure_is_retryable(self, monkeypatch, tmp_path, config):
        def boom(request, timeout=None):
            raise OSError("connection reset")
        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(ClipUnavailable) as caught:
            fetch.download_items("u", info_of(photo()), tmp_path, config)
        assert caught.value.reason == "image_fetch_failed"

    def test_no_items_is_an_error(self, tmp_path, config):
        with pytest.raises(ClipUnavailable):
            fetch.download_items("u", info_of(), tmp_path, config)


class TestAlbumBudget:
    """A carousel of videos could otherwise cost a home connection dearly."""

    def test_stops_once_the_budget_is_spent(self, monkeypatch, tmp_path, config):
        half = b"x" * (ALBUM_TOTAL_BYTES // 2 + 1)
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(half))
        monkeypatch.setattr(fetch, "PHOTO_UPLOAD_LIMIT_BYTES", ALBUM_TOTAL_BYTES)
        downloaded = fetch.download_items(
            "u", info_of(photo(), photo(), photo()), tmp_path, config)
        assert len(downloaded) == 1, "second item would exceed the budget"

    def test_a_single_large_item_is_still_sent(self, monkeypatch, tmp_path, config):
        """The budget bounds albums, not single posts."""
        big = b"x" * (ALBUM_TOTAL_BYTES + 1)
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(big))
        monkeypatch.setattr(fetch, "PHOTO_UPLOAD_LIMIT_BYTES", ALBUM_TOTAL_BYTES * 2)
        assert len(fetch.download_items("u", info_of(photo()), tmp_path, config)) == 1


class FakeYdl:
    """Records which path was taken: reuse or a fresh extraction."""

    def __init__(self, reuse_raises=None):
        self.reuse_raises = reuse_raises
        self.reused = False
        self.re_extracted = False

    def process_ie_result(self, info, download=False):
        self.reused = True
        if self.reuse_raises is not None:
            raise self.reuse_raises

    def download(self, urls):
        self.re_extracted = True


class TestMetadataReuse:
    def test_reuses_the_probe_result(self):
        """Skipping the second extraction is most of the speed win."""
        ydl = FakeYdl()
        fetch.fetch_into(ydl, "https://x/y", {"id": "x"})
        assert ydl.reused and not ydl.re_extracted

    def test_falls_back_when_the_result_is_stale(self):
        """Media URLs are signed and expire; re-extracting is the recovery."""
        ydl = FakeYdl(reuse_raises=KeyError("extractor"))
        fetch.fetch_into(ydl, "https://x/y", {"id": "x"})
        assert ydl.reused and ydl.re_extracted

    def test_extracts_normally_when_there_is_no_result(self):
        ydl = FakeYdl()
        fetch.fetch_into(ydl, "https://x/y", None)
        assert ydl.re_extracted and not ydl.reused


class TestPartialFailures:
    def test_one_bad_item_does_not_lose_the_carousel(self, monkeypatch, tmp_path, config):
        payloads = iter([b"ok", b"x" * (PHOTO_UPLOAD_LIMIT_BYTES + 1), b"ok"])

        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *exc): return False

        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda request, timeout=None: Response(next(payloads)))
        downloaded = fetch.download_items(
            "u", info_of(photo(), photo(), photo()), tmp_path, config)
        assert len(downloaded) == 2, "the oversized middle item should be skipped"

    def test_a_single_bad_item_still_fails(self, monkeypatch, tmp_path, config):
        too_big = b"x" * (PHOTO_UPLOAD_LIMIT_BYTES + 1)
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(too_big))
        with pytest.raises(ClipRejected):
            fetch.download_items("u", info_of(photo()), tmp_path, config)


class TestUnsafeImageUrls:
    """The image fetcher is ours, so yt-dlp's extractor allowlist does not cover it."""

    def test_an_internal_address_is_refused(self, monkeypatch, tmp_path, config):
        monkeypatch.setattr("src.core.urlguard.resolved_addresses",
                            lambda host: ["192.168.1.1"])
        with pytest.raises(ClipRejected) as caught:
            fetch.download_items("u", info_of(photo()), tmp_path, config)
        assert caught.value.reason == "unsafe_url"

    def test_one_unsafe_item_does_not_lose_the_album(self, monkeypatch, tmp_path, config):
        addresses = iter([["192.168.1.1"], ["93.184.216.34"], ["93.184.216.34"]])
        monkeypatch.setattr("src.core.urlguard.resolved_addresses",
                            lambda host: next(addresses))
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(b"ok"))
        downloaded = fetch.download_items(
            "u", info_of(photo(), photo(), photo()), tmp_path, config)
        assert len(downloaded) == 2
