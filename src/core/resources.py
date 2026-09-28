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
CGROUP = Path("/sys/fs/cgroup")
# (limit, usage, stat, reclaimable-cache field) for cgroup v2, then v1. A limit
# of "max" (v2) fails to parse and is skipped; v1 reports "no limit" as a huge
# number instead.
CGROUP_MEMORY_FILES = (
    (CGROUP / "memory.max", CGROUP / "memory.current",
     CGROUP / "memory.stat", "inactive_file"),
    (CGROUP / "memory/memory.limit_in_bytes", CGROUP / "memory/memory.usage_in_bytes",
     CGROUP / "memory/memory.stat", "total_inactive_file"),
)
UNLIMITED_CGROUP_BYTES = 1 << 60
BYTES_PER_MB = 1024 * 1024


def free_disk_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def _host_available_bytes() -> int | None:
    try:
        for line in MEMINFO.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _stat_field(path: Path, name: str) -> int:
    try:
        for line in path.read_text().splitlines():
            key, _, value = line.partition(" ")
            if key == name:
                return int(value)
    except (OSError, ValueError):
        pass
    return 0


def _container_available_bytes() -> int | None:
    """Headroom under the container's memory limit, or None when uncapped.

    Inside a container /proc/meminfo describes the host, so a 512 MB cap is
    invisible there and the process is OOM-killed long before the floor trips.
    Page cache the kernel can reclaim (inactive_file) is not counted as used,
    the same way `docker stats` reports it.
    """
    for limit_file, usage_file, stat_file, cache_field in CGROUP_MEMORY_FILES:
        limit = _read_int(limit_file)
        usage = _read_int(usage_file)
        if limit is None or usage is None:
            continue
        if limit >= UNLIMITED_CGROUP_BYTES:
            return None
        reclaimable = _stat_field(stat_file, cache_field)
        return max(limit - (usage - reclaimable), 0)
    return None


def available_memory_bytes() -> int | None:
    """Linux only. The tighter of the host and the container, so a capped
    container is judged by its cap. Returns None where it cannot be
    determined, so callers skip the check rather than guess."""
    known = [value for value in (_host_available_bytes(), _container_available_bytes())
             if value is not None]
    return min(known) if known else None


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
