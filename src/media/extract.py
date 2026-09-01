"""Reading a post's metadata and turning it into a list of media items."""

import logging

import yt_dlp

from src.core.config import Config
from src.core.errors import ClipRejected, as_user_error
from src.core.limits import ALBUM_MAX_ITEMS
from src.core.models import ClipInfo, MediaItem, MediaKind
from src.media import twitter_photos
from src.media.links import platform_of
from src.media.ytdlp import media_options

log = logging.getLogger(__name__)

# Failures a logged-in session can plausibly fix. Retrying a network timeout or
# an over-long clip with cookies would just waste a request.
COOKIE_RETRY_REASONS = frozenset({"needs_login", "private", "age_restricted"})


def _as_clock(seconds: int) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m{remainder:02d}s" if minutes else f"{remainder}s"


def _has_playable_media(entry: dict) -> bool:
    return bool(entry.get("formats") or entry.get("url"))


def _best_image_url(entry: dict) -> str | None:
    """yt-dlp orders thumbnails worst to best, so the last one is full size."""
    thumbnails = [t for t in (entry.get("thumbnails") or []) if t.get("url")]
    if thumbnails:
        return thumbnails[-1]["url"]
    return entry.get("thumbnail")


def _clip_key(raw: dict) -> str:
    extractor = raw.get("extractor_key") or raw.get("extractor") or "unknown"
    identifier = raw.get("id") or raw.get("webpage_url") or "unknown"
    return f"{extractor}:{identifier}"


def _video_item(entry: dict) -> MediaItem:
    return MediaItem(
        kind=MediaKind.VIDEO,
        raw=entry,
        duration_seconds=int(entry.get("duration") or 0),
        width=int(entry.get("width") or 0),
        height=int(entry.get("height") or 0),
    )


def _items_from(entries: list[dict], config: Config) -> tuple[list[MediaItem], list[int]]:
    """Build the media list, reporting any videos dropped for being too long."""
    items: list[MediaItem] = []
    too_long: list[int] = []

    for entry in entries:
        if _has_playable_media(entry):
            item = _video_item(entry)
            if item.duration_seconds > config.max_duration_seconds:
                too_long.append(item.duration_seconds)
                continue
            items.append(item)
            continue
        image_url = _best_image_url(entry)
        if image_url:
            items.append(MediaItem(kind=MediaKind.PHOTO, image_url=image_url))

    return items, too_long


def _x_photo_items(url: str) -> list[MediaItem]:
    """yt-dlp exposes no media for an X photo post; X's own endpoint does."""
    return [MediaItem(kind=MediaKind.PHOTO, image_url=image_url)
            for image_url in twitter_photos.photo_urls(url)]


def probe(url: str, config: Config) -> ClipInfo:
    """Read metadata without downloading, so a long video costs us nothing.

    Anonymous first. Cookies are tried only when the post failed for a reason a
    session could fix, which keeps authenticated traffic to the minimum — that
    is what stops the account being flagged.
    """
    try:
        return _probe(url, config, with_cookies=False)
    except ClipRejected as error:
        if not (config.cookies_file and error.reason in COOKIE_RETRY_REASONS):
            raise
        log.info("retrying %s with cookies (%s)", url, error.reason)
        return _probe(url, config, with_cookies=True)


def _probe(url: str, config: Config, *, with_cookies: bool) -> ClipInfo:
    try:
        with yt_dlp.YoutubeDL(media_options(config, with_cookies=with_cookies)) as ydl:
            raw = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as error:
        log.warning("yt-dlp probe failed for %s: %s", url,
                    str(error).replace("\n", " ")[:300])
        raise as_user_error(error) from error

    if raw is None:
        raise ClipRejected("nothing downloadable at that link", "no_media")

    entries = [entry for entry in (raw.get("entries") or []) if entry] or [raw]
    items, too_long = _items_from(entries, config)

    if not items and platform_of(url) == "twitter":
        items = _x_photo_items(url)

    if not items:
        if too_long:
            raise ClipRejected(
                f"clip is {_as_clock(max(too_long))}, "
                f"limit is {_as_clock(config.max_duration_seconds)}",
                "too_long",
            )
        raise ClipRejected("no video or image in that post", "no_media")

    if len(items) > ALBUM_MAX_ITEMS:
        log.info("post has %d items, sending the first %d", len(items), ALBUM_MAX_ITEMS)

    return ClipInfo(
        key=_clip_key(entries[0]),
        title=raw.get("title") or entries[0].get("title") or "post",
        items=tuple(items[:ALBUM_MAX_ITEMS]),
        used_cookies=with_cookies,
    )
