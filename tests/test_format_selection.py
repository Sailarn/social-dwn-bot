"""Which format yt-dlp picks, run through its real selector.

The formats are one reel's actual lists (URLs stripped), captured anonymously
and with cookies. With cookies Instagram reports sizes for its DASH streams, and
those streams are all VP9 — which many Telegram clients cannot play: the video
arrives as a frozen first frame with sound.
"""

import json
from pathlib import Path

import pytest
import yt_dlp

from src.core.config import Config
from src.media.ytdlp import media_options

FORMATS = json.loads(
    (Path(__file__).parent / "fixtures" / "instagram_formats.json").read_text())
UNPLAYABLE_CODECS = ("vp8", "vp9", "vp09", "av01")


def chosen(formats: list[dict]) -> list[dict]:
    options = media_options(Config(bot_token="x"))
    with yt_dlp.YoutubeDL(options) as ydl:
        selector = ydl.build_format_selector(options["format"])
        picked = next(iter(selector({
            "formats": formats,
            "has_merged_format": True,
            "incomplete_formats": False,
        })))
    return picked.get("requested_formats") or [picked]


def video_codec(formats: list[dict]) -> str:
    return " ".join(str(f.get("vcodec")) for f in formats)


@pytest.mark.parametrize("mode", ["anonymous", "cookies"])
def test_instagram_never_gets_vp9(mode):
    picked = chosen(FORMATS[mode])
    assert not video_codec(picked).startswith(UNPLAYABLE_CODECS), picked


def test_the_cookie_path_takes_the_progressive_mp4():
    """The progressive MP4s are H.264 + AAC (ffprobed): playable everywhere."""
    picked = chosen(FORMATS["cookies"])
    assert [f["format_id"] for f in picked][0].startswith("18458446648188626v-")


def test_h264_dash_is_still_preferred_when_offered():
    """X and TikTok label their codecs; an H.264 stream is fine to take."""
    formats = [
        {"format_id": "h264", "ext": "mp4", "vcodec": "avc1.64001F", "acodec": "none",
         "filesize": 5_000_000, "height": 720, "url": "https://cdn.invalid/v"},
        {"format_id": "vp9", "ext": "mp4", "vcodec": "vp09.00.40.08", "acodec": "none",
         "filesize": 4_000_000, "height": 1080, "url": "https://cdn.invalid/vp9"},
        {"format_id": "aac", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
         "filesize": 200_000, "url": "https://cdn.invalid/a"},
    ]
    assert [f["format_id"] for f in chosen(formats)] == ["h264", "aac"]
