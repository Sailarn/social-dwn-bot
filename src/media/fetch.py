"""Getting the actual bytes: video via yt-dlp, images over plain HTTP."""

import logging
import re
import urllib.request
from pathlib import Path

import yt_dlp

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable, as_user_error
from src.core.limits import (
    ALBUM_TOTAL_BYTES,
    IMAGE_FETCH_TIMEOUT_SECONDS,
    IMAGE_USER_AGENT,
    PHOTO_UPLOAD_LIMIT_BYTES,
)
from src.core.models import ClipInfo, DownloadedItem, MediaItem
from src.core.resources import ensure_disk
from src.core.urlguard import UnsafeUrl, ensure_safe
from src.media.transcode import shrink_to_limit
from src.media.ytdlp import media_options

log = logging.getLogger(__name__)

UNSAFE_FILENAME_CHARS = re.compile(r"[^\w.-]")


def fetch_into(ydl: yt_dlp.YoutubeDL, url: str, raw: dict | None) -> None:
    """Download using the probe's result, falling back to a fresh extraction.

    Reusing the result skips a second metadata round trip, which is most of the
    wall time and half the requests we make to the site. The result can go stale
    though — media URLs are signed and expire — so a failure re-extracts rather
    than giving up.
    """
    if raw is None:
        ydl.download([url])
        return
    try:
        ydl.process_ie_result(dict(raw), download=True)
    except Exception as error:
        # Any failure to reuse the result is recoverable, because re-extracting
        # is exactly what we would have done anyway. Catch broadly: a stale or
        # malformed result raises several different types.
        log.info("could not reuse metadata for %s (%s), re-extracting",
                 url, type(error).__name__)
        ydl.download([url])


def _download_video(url: str, item: MediaItem, destination: Path,
                    config: Config, *, with_cookies: bool) -> Path:
    try:
        with yt_dlp.YoutubeDL(
            media_options(config, destination, with_cookies=with_cookies)
        ) as ydl:
            fetch_into(ydl, url, item.raw)
    except yt_dlp.utils.DownloadError as error:
        log.warning("yt-dlp download failed for %s: %s", url,
                    str(error).replace("\n", " ")[:300])
        raise as_user_error(error) from error

    files = [entry for entry in destination.iterdir() if entry.is_file()]
    if not files:
        raise ClipUnavailable("yt-dlp produced no file", "no_file")
    path = max(files, key=lambda entry: entry.stat().st_size)

    if path.stat().st_size > config.max_filesize_bytes:
        path = shrink_to_limit(path, item.duration_seconds, config)
    return path


def _download_image(item: MediaItem, destination: Path, index: int) -> Path:
    # We fetch this URL ourselves, so yt-dlp's extractor allowlist does not
    # cover it. The URL came from a platform's JSON or a third-party mirror.
    try:
        ensure_safe(item.image_url)
    except UnsafeUrl as error:
        raise ClipRejected(f"refusing that image: {error}", "unsafe_url") from error

    target = destination / f"image_{index}.jpg"
    request = urllib.request.Request(
        item.image_url, headers={"User-Agent": IMAGE_USER_AGENT}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=IMAGE_FETCH_TIMEOUT_SECONDS
        ) as response:
            target.write_bytes(response.read())
    except OSError as error:
        raise ClipUnavailable(f"image fetch failed: {error}",
                              "image_fetch_failed") from error

    if target.stat().st_size > PHOTO_UPLOAD_LIMIT_BYTES:
        raise ClipRejected("image is over Telegram's 10 MB photo limit",
                           "photo_too_big")
    return target


def download_items(url: str, info: ClipInfo, destination: Path,
                   config: Config) -> list[DownloadedItem]:
    """Blocking download of every item in the post; call this off the event loop."""
    ensure_disk(destination, config.min_free_disk_mb)
    downloaded: list[DownloadedItem] = []
    used_bytes = 0

    for index, item in enumerate(info.items):
        item_directory = destination / UNSAFE_FILENAME_CHARS.sub("_", f"{index}")
        item_directory.mkdir(parents=True, exist_ok=True)

        try:
            if item.is_video:
                path = _download_video(url, item, item_directory, config,
                                       with_cookies=info.used_cookies)
            else:
                path = _download_image(item, item_directory, index)
        except ClipRejected:
            # One unusable item should not lose the rest of a carousel. A
            # single-item post has nothing to fall back on, so it still fails.
            if not info.is_album:
                raise
            log.info("skipping item %d of %d: not sendable", index + 1, len(info.items))
            continue

        size = path.stat().st_size
        # Keep at least one item: the budget bounds albums, not single posts.
        if downloaded and used_bytes + size > ALBUM_TOTAL_BYTES:
            log.info("album budget reached, sending %d of %d items",
                     len(downloaded), len(info.items))
            break
        used_bytes += size
        downloaded.append(DownloadedItem(path=path, item=item))

    if not downloaded:
        raise ClipUnavailable("nothing could be downloaded", "no_file")
    return downloaded
