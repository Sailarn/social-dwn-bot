"""Re-encoding: a clip over Telegram's upload ceiling, or one in a codec
Telegram's apps will not play."""

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
from src.core.resources import ensure_memory
from src.core.limits import (
    AUDIO_BITRATE_KBPS,
    CONVERTED_MAX_FPS,
    MAX_ENCODED_HEIGHT,
    REENCODE_TIMEOUT_SECONDS,
    SIZE_TARGET_MARGIN_BYTES,
)

log = logging.getLogger(__name__)

MIN_VIABLE_VIDEO_KBPS = 100
PROBE_TIMEOUT_SECONDS = 30

# What every Telegram app plays inline. VP9 and AV1 in an MP4 show as a frozen
# first frame on many phones.
PLAYABLE_VIDEO_CODECS = frozenset({"h264"})
# Quality-targeted rather than bitrate-targeted: these are short clips, far
# under the size ceiling, and a constant quality is cheaper to reason about.
CONVERTED_CRF = 26

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


def video_codec(path: Path) -> str | None:
    """The first video stream's codec name, or None if it cannot be read."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             # One bare value per line: csv output can carry a trailing comma
             # ("vp9,") when the stream has side data.
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = result.stdout.split()
    return lines[0] if lines else None


def _playable_command(video: Path, audio: Path | None, target: Path,
                      convert: bool) -> list[str]:
    inputs = ["-i", str(video)] + (["-i", str(audio)] if audio else [])
    # With separate audio take it from the second input; otherwise keep the
    # video's own audio if it has any ("?" makes it optional).
    audio_map = ["-map", "1:a:0"] if audio else ["-map", "0:a:0?"]
    if convert:
        codecs = [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CONVERTED_CRF),
            "-pix_fmt", "yuv420p",
            "-vf", f"scale=-2:'min({MAX_ENCODED_HEIGHT},ih)'",
            "-fpsmax", str(CONVERTED_MAX_FPS),
            "-c:a", "aac", "-b:a", f"{AUDIO_BITRATE_KBPS}k",
        ]
    else:
        codecs = ["-c", "copy"]
    return ["nice", "-n", "10", "ffmpeg", "-y", *inputs, "-map", "0:v:0", *audio_map,
            *codecs, "-movflags", "+faststart", str(target)]


def make_playable(video: Path, audio: Path | None, config: Config) -> Path:
    """An H.264 MP4 with its audio, from whatever a source handed us.

    Already H.264 with its audio inside: returned as is. H.264 with separate
    audio: the two are joined without re-encoding. Anything else is converted,
    which is minutes of CPU on a small host. Failure raises ClipUnavailable, so
    the pipeline can fetch the post another way.
    """
    codec = video_codec(video)
    convert = codec not in PLAYABLE_VIDEO_CODECS
    if not convert and audio is None:
        return video
    if not shutil.which("ffmpeg"):
        raise ClipUnavailable(f"cannot convert {codec or 'unknown'} video without "
                              "ffmpeg", "no_ffmpeg")

    target = video.with_name(f"{video.stem}_playable.mp4")
    started = time.monotonic()
    with _transcode_slots:
        ensure_memory(config.min_free_memory_mb)
        log.info("%s %s video%s", "converting" if convert else "joining",
                 codec or "unknown", " with separate audio" if audio else "")
        try:
            result = subprocess.run(
                _playable_command(video, audio, target, convert),
                capture_output=True, timeout=REENCODE_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired as error:
            raise ClipUnavailable("video conversion timed out", "convert_failed") from error
    if result.returncode != 0 or not target.is_file():
        log.warning("conversion failed: %s", result.stderr.decode(errors="replace")[-300:])
        raise ClipUnavailable("video conversion failed", "convert_failed")
    log.info("made playable in %.0fs (%s -> h264, %d bytes)",
             time.monotonic() - started, codec, target.stat().st_size)
    return target
