"""SSRF guard for URLs we fetch ourselves.

yt-dlp's extractor allowlist does not cover image downloads: those URLs come from
a platform's JSON, or from the fxtwitter mirror, and we request them directly.
"""

import pytest

from src.core.urlguard import UnsafeUrl, ensure_safe


@pytest.fixture
def resolves_to(monkeypatch):
    def apply(addresses):
        monkeypatch.setattr("src.core.urlguard.resolved_addresses",
                            lambda host: addresses)
    return apply


class TestBlocked:
    @pytest.mark.parametrize("address", [
        "192.168.1.1",     # the home router
        "10.0.0.5",
        "172.16.0.1",
        "127.0.0.1",        # the bot's own health endpoint, and the PWA
        "169.254.169.254",  # cloud metadata, if this ever moves to a VPS
        "0.0.0.0",
        "::1",
        "fd00::1",
    ])
    def test_private_and_internal_addresses(self, resolves_to, address):
        resolves_to([address])
        with pytest.raises(UnsafeUrl):
            ensure_safe("https://evil.example/image.jpg")

    def test_a_host_that_resolves_to_both_is_still_refused(self, resolves_to):
        """A DNS-rebinding style answer must not pass on the public entry."""
        resolves_to(["93.184.216.34", "127.0.0.1"])
        with pytest.raises(UnsafeUrl):
            ensure_safe("https://mixed.example/image.jpg")

    def test_a_host_that_does_not_resolve(self, resolves_to):
        resolves_to([])
        with pytest.raises(UnsafeUrl):
            ensure_safe("https://nowhere.example/image.jpg")

    @pytest.mark.parametrize("url", [
        "http://pbs.twimg.com/media/a.jpg",   # plaintext
        "file:///etc/passwd",
        "ftp://example.com/a.jpg",
        "https:///no-host.jpg",
    ])
    def test_schemes_and_shapes(self, url, resolves_to):
        resolves_to(["93.184.216.34"])
        with pytest.raises(UnsafeUrl):
            ensure_safe(url)


class TestAllowed:
    @pytest.mark.parametrize("url", [
        "https://pbs.twimg.com/media/HRB5d4dakAAvr5G.jpg?name=orig",
        "https://scontent.cdninstagram.com/v/t51/photo.jpg",
    ])
    def test_real_cdn_urls_pass(self, url, resolves_to):
        resolves_to(["93.184.216.34"])
        ensure_safe(url)

    def test_public_ipv6_passes(self, resolves_to):
        resolves_to(["2606:2800:220:1:248:1893:25c8:1946"])
        ensure_safe("https://example.com/a.jpg")
