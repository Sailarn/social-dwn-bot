"""Re-encoding a clip that came down over Telegram's upload ceiling."""

import logging
import shutil
import subprocess
import threading
from pathlib import Path

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
from src.core.resources import ensure_memory
from src.core.limits import (
    AUDIO_BITRATE_KBPS,
    MAX_ENCODED_HEIGHT,
    REENCODE_TIMEOUT_SECONDS,
    SIZE_TARGET_MARGIN_BYTES,
)

log = logging.getLogger(__name__)

MIN_VIABLE_VIDEO_KBPS = 100

# A re-encode is minutes of pegged CPU. Two at once on a Pi, alongside whatever
# else the machine runs, is how it falls over. Threading, not asyncio, because
# the work happens in a worker thread.
_transcode_slots = threading.Semaphore(1)


def configure(max_concurrent: int) -> None:
    global _transcode_slots
    _transcode_slots = threading.Semaphore(max(max_concurrent, 1))


def _bitrate_budget_kbps(duration_seconds: int, config: Config) -> int:
    budget_bits = (config.max_filesize_bytes - SIZE_TARGET_MARGIN_BYTES) * 8
    return int(budget_bits / duration_seconds / 1000) - AUDIO_BITRATE_KBPS


def _ffmpeg_command(source: Path, target: Path, video_kbps: int) -> list[str]:
    return [
        # Niced: a re-encode is minutes of pegged CPU on a Pi, and the bot shares
        # the machine with other applications.
        "nice", "-n", "10",
        "ffmpeg", "-y", "-i", str(source),
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
        "-vf", f"scale=-2:'min({MAX_ENCODED_HEIGHT},ih)'",
        "-c:a", "aac", "-b:a", f"{AUDIO_BITRATE_KBPS}k",
        "-movflags", "+faststart",
        str(target),
    ]


def shrink_to_limit(source: Path, duration_seconds: int, config: Config) -> Path:
    """Re-encode an oversized clip so it fits Telegram's upload ceiling."""
    if not shutil.which("ffmpeg"):
        raise ClipRejected("clip is over the size limit and ffmpeg is unavailable", "no_ffmpeg")
    if duration_seconds <= 0:
        raise ClipRejected("clip is over the size limit and has no known duration", "too_large")

    video_kbps = _bitrate_budget_kbps(duration_seconds, config)
    if video_kbps < MIN_VIABLE_VIDEO_KBPS:
        raise ClipRejected("clip is too long to fit under the size limit", "too_long_to_fit")

    target = source.with_name(f"{source.stem}_fit.mp4")
    with _transcode_slots:
        ensure_memory(config.min_free_memory_mb)
        log.info("re-encoding %s to ~%dkbps", source.name, video_kbps)
        result = subprocess.run(
            _ffmpeg_command(source, target, video_kbps),
            capture_output=True, timeout=REENCODE_TIMEOUT_SECONDS, check=False,
        )
    if result.returncode != 0 or not target.is_file():
        raise ClipUnavailable("re-encode failed", "reencode_failed")

    source.unlink(missing_ok=True)
    if target.stat().st_size > config.max_filesize_bytes:
        raise ClipRejected("clip stays over the size limit after re-encoding", "too_large")
    return target
