"""Which links the bot picks up, and how they collapse to one cache key."""

import pytest

from src.media.links import find_supported_link, normalize_url


@pytest.mark.parametrize("text", [
    "https://www.instagram.com/reel/Cx1y2z3/",
    "https://www.instagram.com/p/Cx1y2z3AbCd/?igsi=abc123tracking",
    "https://instagr.am/p/Cx1y2z3/",
    "https://x.com/user/status/1234567890",
    "https://twitter.com/user/status/1234567890?s=20",
    "https://t.co/abcdefg",
    "https://vm.tiktok.com/ZMabcdef/",
    "https://vt.tiktok.com/ZSabcdef/",
    "https://www.tiktok.com/@user/video/7300000000000000000?_t=x&_r=1",
    "look at this https://www.instagram.com/reel/Cx1y2z3/ nice",
])
def test_supported_links_are_found(text):
    assert find_supported_link(text) is not None


@pytest.mark.parametrize("text", [
    "https://www.youtube.com/shorts/wYwmcYAy-eI",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://vimeo.com/123456",
    "https://example.com/video.mp4",
    "just a normal message",
    "",
])
def test_unsupported_links_are_ignored(text):
    assert find_supported_link(text) is None


def test_youtube_is_excluded_on_purpose():
    """Telegram embeds a player for YouTube, so a copy earns nothing."""
    assert find_supported_link("https://www.youtube.com/shorts/abc123") is None


def test_trailing_punctuation_is_stripped():
    assert find_supported_link("(https://www.instagram.com/p/Cx1y2z3/)") == \
        "https://www.instagram.com/p/Cx1y2z3/"
    assert find_supported_link("see https://x.com/u/status/1,") == \
        "https://x.com/u/status/1"


def test_only_the_first_link_is_taken():
    found = find_supported_link(
        "https://x.com/u/status/1 and https://vm.tiktok.com/ZMabc/")
    assert found == "https://x.com/u/status/1"


@pytest.mark.parametrize("raw,expected", [
    ("https://www.instagram.com/p/X/?igsi=abc", "https://instagram.com/p/X"),
    ("https://instagram.com/p/X", "https://instagram.com/p/X"),
    ("https://www.tiktok.com/@u/video/730?_t=x&_r=1", "https://tiktok.com/@u/video/730"),
    ("https://x.com/u/status/123?s=20", "https://x.com/u/status/123"),
    ("https://X.COM/u/status/123", "https://x.com/u/status/123"),
    ("https://instagram.com/p/X/#fragment", "https://instagram.com/p/X"),
])
def test_normalisation(raw, expected):
    assert normalize_url(raw) == expected


def test_tracking_params_collapse_to_one_key():
    """Two shares of the same post must hit a single cache entry."""
    assert normalize_url("https://www.instagram.com/p/X/?igsi=1") == \
           normalize_url("https://instagram.com/p/X?igsi=2")
