"""Retrying the operations that are worth retrying."""

import asyncio
import logging
import subprocess

from src.core.errors import ClipRejected, ClipUnavailable

log = logging.getLogger(__name__)

FIRST_DELAY_SECONDS = 2


async def with_retries(operation, attempts: int, label: str):
    """Retry a blocking operation with backoff. ClipRejected is never retried."""
    delay_seconds = FIRST_DELAY_SECONDS
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.to_thread(operation)
        except ClipRejected:
            raise
        except (ClipUnavailable, OSError, subprocess.SubprocessError) as error:
            last_error = error
            log.warning("%s attempt %d/%d failed: %s", label, attempt, attempts, error)
            if attempt < attempts:
                await asyncio.sleep(delay_seconds)
                delay_seconds *= 2

    raise ClipUnavailable(
        str(last_error) if last_error else "gave up",
        getattr(last_error, "reason", None),
    )
