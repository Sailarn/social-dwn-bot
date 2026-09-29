"""How yt-dlp is configured, in one place.

Probing and downloading must agree on these options, because the download reuses
the result the probe already extracted.
"""

import logging
from pathlib import Path

from src.core.config import Config
from src.core.limits import SIZE_TARGET_MARGIN_BYTES

# Only these extractors may run. This is the SSRF control: a shortener that
# redirects somewhere unexpected lands on yt-dlp's `generic` extractor, which is
# absent from this list, so the fetch never happens. Every link form we actually
# support has a dedicated extractor, including the shorteners:
#   vm.tiktok.com -> vm.tiktok      t.co -> twitter:shortener
ALLOWED_EXTRACTORS = [
    "Instagram",
    "InstagramIOS",
    "TikTok",
    "vm.tiktok",
    "twitter",
    "twitter:shortener",
    "twitter:amplify",
    "twitter:card",
]


class _YtdlpLog:
    """yt-dlp prints failures straight to stderr, duplicating the warning our
    own code logs for them with the request id. Keep its output for debugging
    only, so a post that merely fell back to cookies is not an ERROR line."""

    _log = logging.getLogger("yt_dlp")

    def debug(self, message: str) -> None:
        self._log.debug(message)

    info = warning = error = debug


def _base_options(config: Config, with_cookies: bool) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "logger": _YtdlpLog(),
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "allowed_extractors": ALLOWED_EXTRACTORS,
        # X serves HLS in fragments; fetching a few at once helps there and
        # costs nothing for the progressive MP4s the other sites return.
        "concurrent_fragment_downloads": 4,
    }
    # Cookies are opt-in per request. Every authenticated call spends the
    # account's trust budget, and an account making sustained API calls is what
    # Instagram flags. Anonymous first, cookies only when a post needs them.
    if with_cookies and config.cookies_file:
        options["cookiefile"] = str(config.cookies_file)
    return options


# VP8/VP9/AV1 in an MP4 plays as a frozen first frame with sound in many
# Telegram clients. Instagram's DASH streams are all VP9, so they are refused;
# its progressive MP4s are H.264. The `?` keeps formats with no codec label
# (Instagram's progressive files), which are the ones we want.
PLAYABLE_VIDEO = "[vcodec!^=?vp8][vcodec!^=?vp9][vcodec!^=?vp09][vcodec!^=?av01]"


def _format_selector(size_target_bytes: int) -> str:
    ceiling = f"{size_target_bytes}"
    video = f"bv*{PLAYABLE_VIDEO}"
    combined = f"b{PLAYABLE_VIDEO}"
    return (
        f"{video}[filesize<={ceiling}][ext=mp4]+ba[ext=m4a]/"
        f"{combined}[filesize<={ceiling}][ext=mp4]/"
        f"{video}[filesize_approx<={ceiling}]+ba/"
        f"{combined}[filesize_approx<={ceiling}]/"
        f"{combined}[ext=mp4]/{combined}/b"
    )


def media_options(config: Config, destination: Path | None = None, *,
                  with_cookies: bool = False) -> dict:
    """Options shared by probing and downloading."""
    size_target = max(config.max_filesize_bytes - SIZE_TARGET_MARGIN_BYTES, 1)
    options = _base_options(config, with_cookies) | {
        # Lets a photo post come back as metadata instead of raising, so image
        # posts can be delivered rather than refused.
        "ignore_no_formats_error": True,
        "format": _format_selector(size_target),
        "merge_output_format": "mp4",
        "restrictfilenames": True,
    }
    if destination is not None:
        options["outtmpl"] = str(destination / "%(id)s.%(ext)s")
    return options
