"""Refusing work the machine cannot afford, instead of falling over.

The bot shares a Raspberry Pi with other applications. Running it out of disk or
memory takes those down too, so a request that would push the machine past a
floor is declined with a clear message.
"""

import logging
import shutil
from pathlib import Path

from src.core.errors import ClipUnavailable

log = logging.getLogger(__name__)

MEMINFO = Path("/proc/meminfo")
BYTES_PER_MB = 1024 * 1024


def free_disk_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def available_memory_bytes() -> int | None:
    """Linux only. Returns None where it cannot be determined, so callers skip
    the check rather than guess."""
    try:
        for line in MEMINFO.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def ensure_disk(path: Path, minimum_mb: int) -> None:
    if minimum_mb <= 0:
        return
    free = free_disk_bytes(path)
    if free < minimum_mb * BYTES_PER_MB:
        log.warning("refusing work: only %.0fMB free on %s", free / BYTES_PER_MB, path)
        raise ClipUnavailable("the machine is low on disk space, try later",
                              "low_disk")


def ensure_memory(minimum_mb: int) -> None:
    if minimum_mb <= 0:
        return
    available = available_memory_bytes()
    if available is None:
        return
    if available < minimum_mb * BYTES_PER_MB:
        log.warning("refusing work: only %.0fMB memory available",
                    available / BYTES_PER_MB)
        raise ClipUnavailable("the machine is low on memory, try later",
                              "low_memory")
