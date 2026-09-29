"""Instagram through Apify's scraper, when our own IP is not welcome.

A datacenter IP gets HTTP 429 from Instagram before it asks for anything, and
the cookie fallback spends a real account on every post. Apify fetches through
its own proxies and hands back the post's media URLs, which we then download
from Instagram's CDN ourselves: the progressive H.264 file every phone plays.
(Should a source ever hand back another codec, it is converted before sending.)

It is slower than a direct request — each one starts a scraper run — and costs
credit, so it sits between the free anonymous attempt and the account-spending
cookie one.

Any failure here is not final: the pipeline moves on to the next step.
"""

import calendar
import json
import logging
import time
import urllib.error
import urllib.request

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
from src.core.limits import (
    ALBUM_MAX_ITEMS,
    APIFY_RUN_TIMEOUT_SECONDS,
    APIFY_TIMEOUT_SECONDS,
)
from src.core.models import ClipInfo, MediaItem, MediaKind, Source
from src.media.links import platform_of

log = logging.getLogger(__name__)

# Returns Instagram's own post object, with the progressive H.264 + AAC video
# (720p) — not the VP9 DASH stream Apify's own Instagram scrapers return, which
# needed minutes of conversion on a small host. Chosen 2026-09-29 against four
# alternatives: cheapest per post (~$0.0045 on the free plan) and most used.
ACTOR = "data-slayer~instagram-post-details"
ENDPOINT = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
HTTP_PAYMENT_REQUIRED = 402
# Apify's error types when the plan's monthly credit is spent.
EXHAUSTED_ERROR_MARKERS = ("usage", "limit-exceeded", "not-enough")

# Credit resets on the 1st; until then every call would fail the same way.
_exhausted_until = 0.0


class CreditsExhausted(ClipUnavailable):
    """The month's credit is spent. `first_time` is set once, for the alert."""

    def __init__(self, first_time: bool):
        super().__init__("apify credits exhausted", "apify_exhausted")
        self.first_time = first_time


def applies_to(url: str, config: Config) -> bool:
    return bool(config.apify_token) and platform_of(url) == "instagram"


def _start_of_next_month(now: float) -> float:
    today = time.gmtime(now)
    year, month = (today.tm_year + 1, 1) if today.tm_mon == 12 else (
        today.tm_year, today.tm_mon + 1)
    return float(calendar.timegm((year, month, 1, 0, 0, 0)))


def _mark_exhausted() -> None:
    global _exhausted_until
    _exhausted_until = _start_of_next_month(time.time())
    log.warning("apify credits exhausted; skipping it until %s",
                time.strftime("%Y-%m-%d", time.gmtime(_exhausted_until)))


def _run(url: str, config: Config) -> list:
    body = json.dumps({"postUrls": [url]}).encode()
    request = urllib.request.Request(
        f"{ENDPOINT}?timeout={APIFY_RUN_TIMEOUT_SECONDS}",
        data=body,
        method="POST",
        headers={
            # In a header, not the query string, so it never lands in a log.
            "Authorization": f"Bearer {config.apify_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=APIFY_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        if error.code == HTTP_PAYMENT_REQUIRED or any(
                marker in detail for marker in EXHAUSTED_ERROR_MARKERS):
            _mark_exhausted()
            raise CreditsExhausted(first_time=True) from error
        raise ClipUnavailable(f"apify returned {error.code}: {detail}",
                              "apify_failed") from error
    except (OSError, ValueError) as error:
        raise ClipUnavailable(f"apify request failed: {error}", "apify_failed") from error


def _video_item(part: dict, config: Config) -> MediaItem | None:
    duration = int(part.get("video_duration") or 0)
    if duration > config.max_duration_seconds:
        return None
    return MediaItem(
        kind=MediaKind.VIDEO,
        video_url=part["video_url"],
        duration_seconds=duration,
        width=int(part.get("original_width") or 0),
        height=int(part.get("original_height") or 0),
    )


def _largest_image(part: dict) -> str | None:
    candidates = [candidate for candidate in
                  (part.get("image_versions") or {}).get("items") or []
                  if candidate.get("url")]
    if candidates:
        return max(candidates, key=lambda candidate: candidate.get("width") or 0)["url"]
    return part.get("thumbnail_url")


def _items_from(post: dict, config: Config) -> tuple[list[MediaItem], bool]:
    """(items, some video was too long). Instagram's own post object: a
    carousel lists its parts in `carousel_media`; a single post is its own part."""
    items: list[MediaItem] = []
    too_long = False
    for part in post.get("carousel_media") or [post]:
        if part.get("video_url"):
            item = _video_item(part, config)
            too_long = too_long or item is None
            if item is not None:
                items.append(item)
        elif image_url := _largest_image(part):
            items.append(MediaItem(kind=MediaKind.PHOTO, image_url=image_url))
    return items, too_long


def _caption(post: dict) -> str:
    caption = post.get("caption")
    text = caption.get("text") if isinstance(caption, dict) else caption
    return (text or "post")[:100]


def probe(url: str, config: Config) -> ClipInfo:
    """Blocking: one scraper run, typically 5-20 s."""
    if time.time() < _exhausted_until:
        raise CreditsExhausted(first_time=False)

    log.info("asking apify for %s", url)
    results = _run(url, config)
    post = results[0] if results and isinstance(results[0], dict) else None
    if post is None or post.get("error") or not post.get("code"):
        reason = (post or {}).get("errorDescription") or (post or {}).get("error")
        raise ClipUnavailable(f"apify found nothing ({reason or 'empty result'})",
                              "apify_failed")

    items, too_long = _items_from(post, config)
    if not items:
        if too_long:
            raise ClipRejected("clip is over the length limit", "too_long")
        raise ClipUnavailable("apify returned no media", "apify_failed")

    return ClipInfo(
        # Same key yt-dlp produces, so the cache matches whichever step fetched it.
        key=f"Instagram:{post['code']}",
        title=_caption(post),
        items=tuple(items[:ALBUM_MAX_ITEMS]),
        source=Source.APIFY,
    )
