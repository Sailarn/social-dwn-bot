"""Self-limiting against ceilings we cannot raise.

Two of them, with different shapes:

* **Telegram** allows roughly 20 messages a minute into one chat. Exceeding it
  earns a 429 with a `retry_after`, so the bot spaces its own sends per chat.
* **The platforms** throttle by IP. Three rapid TikTok extractions already
  produced `Unable to extract universal data for rehydration`. A residential IP
  is a scarce resource here, so requests to one platform are spaced, and a
  throttle response puts that platform in a cooldown.

Both waits happen *before* a download slot is taken, so a paced request does not
occupy capacity while it waits.
"""

import asyncio
import logging
import time

log = logging.getLogger(__name__)


class Pacer:
    """Minimum interval between events sharing a key."""

    def __init__(self, interval_seconds: float):
        self._interval = interval_seconds
        self._next_allowed: dict[object, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, key: object) -> float:
        """Returns how long it waited, for logging and tests.

        A zero interval disables *spacing*, not any reservation already made —
        a cooldown set by PlatformPacer must still be honoured, or turning
        spacing off would quietly remove the throttle protection with it.
        """
        async with self._lock:
            now = time.monotonic()
            earliest = self._next_allowed.get(key, 0.0)
            delay = max(0.0, earliest - now)
            # Reserve the next slot before releasing the lock, so concurrent
            # callers queue behind each other instead of waking together.
            next_slot = max(now, earliest) + self._interval
            if next_slot > now:
                self._next_allowed[key] = next_slot
            else:
                self._next_allowed.pop(key, None)   # nothing pending: forget it
        if delay:
            await asyncio.sleep(delay)
        return delay


class PlatformPacer(Pacer):
    """Spacing plus a cooldown applied when a site tells us to back off."""

    def __init__(self, interval_seconds: float, cooldown_seconds: float):
        super().__init__(interval_seconds)
        self._cooldown = cooldown_seconds

    def penalise(self, platform: str) -> None:
        """Called when a platform throttles us: stop asking for a while."""
        if self._cooldown <= 0:
            return
        resume_at = time.monotonic() + self._cooldown
        self._next_allowed[platform] = max(
            self._next_allowed.get(platform, 0.0), resume_at)
        log.warning("%s throttled us; pausing requests to it for %.0fs",
                    platform, self._cooldown)
