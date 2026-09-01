"""What the bot knows about a post before and after fetching it."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class MediaKind(Enum):
    VIDEO = "video"
    PHOTO = "photo"
    ALBUM = "album"


@dataclass(frozen=True)
class MediaItem:
    """One piece of media in a post. A carousel has several, mixed freely."""

    kind: MediaKind
    # Photos carry a direct URL; videos carry yt-dlp's own result so the
    # download can reuse it instead of extracting a second time.
    image_url: str | None = None
    raw: dict | None = field(default=None, repr=False, compare=False)
    duration_seconds: int = 0
    width: int = 0
    height: int = 0

    @property
    def is_video(self) -> bool:
        return self.kind is MediaKind.VIDEO


@dataclass(frozen=True)
class ClipInfo:
    key: str
    title: str
    items: tuple[MediaItem, ...] = ()
    # Media URLs from an authenticated probe are bound to that session, so the
    # download has to use the same mode.
    used_cookies: bool = False

    @property
    def kind(self) -> MediaKind:
        """What to record and cache this as."""
        if len(self.items) > 1:
            return MediaKind.ALBUM
        if self.items and self.items[0].is_video:
            return MediaKind.VIDEO
        return MediaKind.PHOTO

    @property
    def is_album(self) -> bool:
        return len(self.items) > 1

    @property
    def only(self) -> MediaItem:
        """The single item, for the common one-media post."""
        return self.items[0]

    @property
    def has_video(self) -> bool:
        return any(item.is_video for item in self.items)


@dataclass(frozen=True)
class DownloadedItem:
    path: Path
    item: MediaItem

