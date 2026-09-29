"""Putting media into a chat."""

import logging

from aiogram.types import FSInputFile, Message
from aiogram.utils.media_group import MediaGroupBuilder

from src.core.models import DownloadedItem, LinkedItem, MediaKind

log = logging.getLogger(__name__)

# Cached values carry the media kind so a cache hit can be sent without
# re-fetching metadata to find out what it is.
CACHE_VALUE_SEPARATOR = "|"


def cache_value(kind: MediaKind, file_id: str) -> str:
    return f"{kind.value}{CACHE_VALUE_SEPARATOR}{file_id}"


async def send_cached(message: Message, cached: str) -> MediaKind:
    kind, separator, file_id = cached.partition(CACHE_VALUE_SEPARATOR)
    if not separator:
        # Rows written before photos existed are bare video file_ids.
        kind, file_id = MediaKind.VIDEO.value, cached
    if kind == MediaKind.PHOTO.value:
        await message.reply_photo(file_id)
        return MediaKind.PHOTO
    await message.reply_video(file_id, supports_streaming=True)
    return MediaKind.VIDEO


Sendable = DownloadedItem | LinkedItem


def _input(entry: Sendable) -> FSInputFile | str:
    """A file we upload, or a URL Telegram fetches itself."""
    if isinstance(entry, LinkedItem):
        return entry.url
    return FSInputFile(entry.path)


async def send_one(message: Message, entry: Sendable) -> str | None:
    """A single-media post keeps the richer video treatment."""
    item = entry.item
    if not item.is_video:
        sent = await message.reply_photo(_input(entry))
        return sent.photo[-1].file_id if sent.photo else None

    sent = await message.reply_video(
        _input(entry),
        duration=item.duration_seconds or None,
        width=item.width or None,
        height=item.height or None,
        supports_streaming=True,
    )
    return sent.video.file_id if sent.video else None


async def send_album(message: Message, entries: list[Sendable]) -> None:
    """Telegram media groups mix photos and videos, so a carousel arrives whole.

    Each item gets its own file_id, so albums are sent but not cached.
    """
    album = MediaGroupBuilder()
    for entry in entries:
        media = _input(entry)
        if entry.item.is_video:
            album.add_video(media=media)
        else:
            album.add_photo(media=media)
    await message.reply_media_group(album.build())
