"""Stand-ins for the two network edges.

Shared by the test suite and `scripts/loadtest.py`, so both drive the real code
without reaching Instagram, TikTok, X or Telegram.
"""

import time
from pathlib import Path
from urllib.parse import urlparse

from src.core.models import ClipInfo, DownloadedItem, MediaItem, MediaKind


class FakeSent:
    def __init__(self, file_id: str):
        self.video = type("V", (), {"file_id": file_id})()
        self.photo = [type("P", (), {"file_id": file_id})()]


class FakeMessage:
    """Records what would have been sent, and to whom."""

    def __init__(self, chat_id: int = -100, user_id: int = 1, text: str = ""):
        self.chat = type("C", (), {"id": chat_id})()
        self.from_user = type("U", (), {"id": user_id})()
        self.text = text
        self.caption = None
        self.replies: list[str] = []
        self.videos: list = []
        self.photos: list = []
        self.albums: list = []

    @property
    def sends(self) -> int:
        return len(self.videos) + len(self.photos) + len(self.albums)

    async def reply(self, text, **kwargs):
        self.replies.append(text)

    async def reply_video(self, media, **kwargs):
        self.videos.append(media)
        return FakeSent("VIDEOID")

    async def reply_photo(self, media, **kwargs):
        self.photos.append(media)
        return FakeSent("PHOTOID")

    async def reply_media_group(self, media, **kwargs):
        self.albums.append(media)
        return [FakeSent("ALBUMID")]


class FakeBot:
    def __init__(self):
        self.actions: list = []

    async def send_chat_action(self, chat_id, action):
        self.actions.append((chat_id, action))


def video_info(key="Instagram:abc", duration=15):
    return ClipInfo(key=key, title="t", items=(
        MediaItem(kind=MediaKind.VIDEO, raw={"id": "x"},
                  duration_seconds=duration, width=720, height=1280),))


def album_info(key="Instagram:album", photos=3):
    return ClipInfo(key=key, title="t", items=tuple(
        MediaItem(kind=MediaKind.PHOTO, image_url=f"http://x/{i}.jpg")
        for i in range(photos)))


def clip_key_for(url: str) -> str:
    """Mimic a real extractor: the same post is the same id however it is linked.

    Deriving this naively (split on "/") gives an empty key for a trailing slash
    and the query string for a tracked link, which silently breaks cache tests.
    """
    path = urlparse(url).path
    segments = [segment for segment in path.split("/") if segment]
    return f"Fake:{segments[-1] if segments else 'x'}"


def make_probe(delay_seconds: float = 0.0, error=None, info=None, calls=None):
    """Blocking, like the real probe: it runs in a worker thread."""

    def probe(url: str, config) -> ClipInfo:
        if calls is not None:
            calls.append(url)
        if delay_seconds:
            time.sleep(delay_seconds)
        if error is not None:
            raise error
        return info if info is not None else video_info(key=clip_key_for(url))

    return probe


def make_download(delay_seconds: float = 0.0, size_bytes: int = 1024, error=None,
                  calls=None):
    def download_items(url, info, destination: Path, config):
        if calls is not None:
            calls.append(url)
        if delay_seconds:
            time.sleep(delay_seconds)
        if error is not None:
            raise error
        results = []
        for index, item in enumerate(info.items):
            path = destination / f"item{index}.bin"
            path.write_bytes(b"\0" * size_bytes)
            results.append(DownloadedItem(path=path, item=item))
        return results

    return download_items


class FakeNotifier:
    """Records alerts instead of sending them."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        self.sent.append(text)
        return True
