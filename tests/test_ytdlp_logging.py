"""yt-dlp's own output stays out of the error stream."""

import asyncio

import pytest
import yt_dlp

from src import app
from src.core.config import Config
from src.media.ytdlp import media_options


def test_a_failed_extraction_prints_nothing(capsys):
    """Our code logs the failure with the request id; yt-dlp repeating it on
    stderr made every cookie fallback a red ERROR line in the host's log."""
    with (yt_dlp.YoutubeDL(media_options(Config(bot_token="x"))) as ydl,
          pytest.raises(yt_dlp.utils.DownloadError)):
        ydl.extract_info("https://example.com/not-a-post", download=False)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_shutdown_cancels_leftover_tasks():
    loop = asyncio.new_event_loop()
    try:
        leftover = loop.create_task(asyncio.Event().wait())
        loop.run_until_complete(asyncio.sleep(0))
        app._cancel_leftovers(loop)
        assert leftover.cancelled()
    finally:
        loop.close()
