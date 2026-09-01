"""Per-user rate limiting, so one person cannot drain the upstream link.

Kept in memory: the window is an hour and a restart clearing it is harmless.
"""

import time
from collections import defaultdict, deque

SECONDS_PER_HOUR = 3600


class RateLimiter:
    """Sliding one-hour window per user. A limit of 0 disables it entirely."""

    def __init__(self, max_per_hour: int):
        self._max_per_hour = max_per_hour
        self._history: dict[int, deque[float]] = defaultdict(deque)

    def _recent(self, user_id: int) -> deque[float]:
        history = self._history[user_id]
        cutoff = time.monotonic() - SECONDS_PER_HOUR
        while history and history[0] <= cutoff:
            history.popleft()
        return history

    def allow(self, user_id: int) -> bool:
        if self._max_per_hour <= 0:
            return True
        history = self._recent(user_id)
        if len(history) >= self._max_per_hour:
            return False
        history.append(time.monotonic())
        return True

    def seconds_until_free(self, user_id: int) -> int:
        history = self._recent(user_id)
        if not history:
            return 0
        return max(int(history[0] + SECONDS_PER_HOUR - time.monotonic()), 1)
