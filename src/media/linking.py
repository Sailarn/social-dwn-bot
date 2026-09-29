"""Letting Telegram fetch a file itself, instead of downloading and uploading it.

On a 0.1 CPU host the round trip through our own disk is a few seconds of a
delivery. Telegram will fetch a file from a URL — photos up to 5 MB, other
files up to 20 MB, with the right MIME type — and send it as if we had
uploaded it.

Only links we can vouch for without downloading them qualify: Instagram CDN
links from Apify, whose encoding tag says progressive H.264 (not the VP9 DASH
stream that plays as a frozen frame), checked with a HEAD request for type and
size. A post qualifies whole or not at all; anything that does not goes
through the ordinary download.
"""

import base64
import binascii
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from src.core.limits import IMAGE_FETCH_TIMEOUT_SECONDS, IMAGE_USER_AGENT
from src.core.models import ClipInfo, LinkedItem, MediaItem, Source
from src.core.urlguard import UnsafeUrl, ensure_safe

log = logging.getLogger(__name__)

INSTAGRAM_CDN_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")
# Telegram's limits for a file sent by URL.
URL_PHOTO_MAX_BYTES = 5 * 1024 * 1024
URL_VIDEO_MAX_BYTES = 20 * 1024 * 1024
VIDEO_MIME = "video/mp4"
PHOTO_MIME = "image/jpeg"
UNPLAYABLE_ENCODINGS = ("vp9", "vp09", "av1", "av01")


def _encoding_tag(url: str) -> str:
    """Instagram names a video's encoding in the base64 JSON `efg` parameter,
    e.g. "xpv_progressive...720..." or "...dash_r2evevp9...". Empty if absent."""
    efg = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("efg", [""])[0]
    try:
        decoded = base64.urlsafe_b64decode(efg + "=" * (-len(efg) % 4))
        return str(json.loads(decoded).get("vencode_tag") or "").lower()
    except (binascii.Error, ValueError, AttributeError):
        return ""


def _is_playable_video_link(url: str) -> bool:
    tag = _encoding_tag(url)
    return "progressive" in tag and not any(codec in tag for codec in UNPLAYABLE_ENCODINGS)


def _head(url: str) -> tuple[str, int] | None:
    """(MIME type, size) without downloading, or None if unreadable."""
    try:
        ensure_safe(url)
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": IMAGE_USER_AGENT})
        with urllib.request.urlopen(request, timeout=IMAGE_FETCH_TIMEOUT_SECONDS) as response:
            mime = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            return mime, int(response.headers.get("Content-Length") or 0)
    except (UnsafeUrl, OSError, ValueError) as error:
        log.info("cannot link %s: %s", urllib.parse.urlparse(url).hostname, error)
        return None


def _link(item: MediaItem) -> LinkedItem | None:
    url = item.video_url if item.is_video else item.image_url
    host = urllib.parse.urlparse(url or "").hostname or ""
    if not url or not host.endswith(INSTAGRAM_CDN_SUFFIXES) or item.audio_url:
        return None
    if item.is_video and not _is_playable_video_link(url):
        return None
    head = _head(url)
    if head is None:
        return None
    mime, size = head
    expected, limit = ((VIDEO_MIME, URL_VIDEO_MAX_BYTES) if item.is_video
                       else (PHOTO_MIME, URL_PHOTO_MAX_BYTES))
    if mime != expected or not 0 < size <= limit:
        return None
    return LinkedItem(url=url, size=size, item=item)


def link_items(info: ClipInfo) -> list[LinkedItem] | None:
    """Every item as a link Telegram can fetch, or None to download instead.

    Blocking (one HEAD request per item); call it off the event loop.
    """
    if info.source != Source.APIFY:
        return None
    linked = []
    for item in info.items:
        link = _link(item)
        if link is None:
            return None
        linked.append(link)
    return linked
