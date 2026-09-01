"""Which links the bot acts on, and how they collapse to one cache key."""

import re
from urllib.parse import urlparse, urlunparse

# YouTube is deliberately absent: Telegram embeds a working player for it, so
# downloading a copy earns nothing.
SUPPORTED_LINK_PATTERN = re.compile(
    r"https?://(?:[\w-]+\.)*(?:"
    r"instagram\.com|instagr\.am|"
    r"x\.com|twitter\.com|t\.co|"
    r"tiktok\.com"
    r")/\S+",
    re.IGNORECASE,
)

TRAILING_PUNCTUATION = ").,;'\""


def find_supported_link(text: str) -> str | None:
    match = SUPPORTED_LINK_PATTERN.search(text or "")
    return match.group(0).rstrip(TRAILING_PUNCTUATION) if match else None


def normalize_url(url: str) -> str:
    """Strip tracking parameters so the same post shared twice is one cache key."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    # Every supported platform identifies a post by path, so the query string is
    # tracking junk without exception.
    return urlunparse((parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", "", ""))


PLATFORM_BY_HOST_FRAGMENT = (
    ("instagram", "instagram"),
    ("instagr.am", "instagram"),
    ("tiktok", "tiktok"),
    ("x.com", "twitter"),
    ("twitter", "twitter"),
    ("t.co", "twitter"),
)


def platform_of(url: str) -> str:
    """Coarse label for counting, derived before anything is extracted."""
    host = urlparse(url).netloc.lower()
    for fragment, platform in PLATFORM_BY_HOST_FRAGMENT:
        if fragment in host:
            return platform
    return "other"
