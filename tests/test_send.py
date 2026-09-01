"""Sending cached media back, including rows written before photos existed."""

import asyncio

from src.telegram import send as send_module


class FakeMessage:
    def __init__(self):
        self.videos = []
        self.photos = []
        self.replies = []

    async def reply_video(self, file_id, **kwargs):
        self.videos.append((file_id, kwargs))

    async def reply_photo(self, file_id, **kwargs):
        self.photos.append((file_id, kwargs))

    async def reply(self, text, **kwargs):
        self.replies.append(text)


def send(cached: str) -> FakeMessage:
    message = FakeMessage()
    asyncio.run(send_module.send_cached(message, cached))
    return message


def test_cached_video_is_sent_as_a_video():
    message = send(f"video{send_module.CACHE_VALUE_SEPARATOR}FILEID")
    assert message.videos == [("FILEID", {"supports_streaming": True})]
    assert not message.photos


def test_cached_photo_is_sent_as_a_photo():
    message = send(f"photo{send_module.CACHE_VALUE_SEPARATOR}FILEID")
    assert message.photos == [("FILEID", {})]
    assert not message.videos


def test_legacy_rows_without_a_kind_are_sent_as_video():
    """Rows written before photo support are a bare file_id with no separator.

    Getting this wrong sends an empty file_id to Telegram.
    """
    message = send("BAACAgIAAxkDAAMDbareFileId")
    assert message.videos == [("BAACAgIAAxkDAAMDbareFileId", {"supports_streaming": True})]


def test_a_file_id_containing_the_separator_still_works():
    message = send(f"video{send_module.CACHE_VALUE_SEPARATOR}FILE{send_module.CACHE_VALUE_SEPARATOR}ID")
    assert message.videos[0][0] == f"FILE{send_module.CACHE_VALUE_SEPARATOR}ID"
