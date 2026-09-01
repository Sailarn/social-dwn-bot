"""Telling the operator when something new breaks.

Everything is already recorded; this is the part that reaches you without you
going to look. It fires only on a *new* unique error, so a platform breaking
produces one message rather than one per affected post.
"""

import logging
import time
from collections import deque

log = logging.getLogger(__name__)

MAX_ALERTS_PER_HOUR = 6
SECONDS_PER_HOUR = 3600


class Notifier:
    """Sends to one chat, and refuses to become the problem itself."""

    def __init__(self, bot, chat_id: int | None,
                 max_per_hour: int = MAX_ALERTS_PER_HOUR):
        self._bot = bot
        self._chat_id = chat_id
        self._max_per_hour = max_per_hour
        self._sent: deque[float] = deque()

    @property
    def enabled(self) -> bool:
        return self._chat_id is not None and self._bot is not None

    def _within_budget(self) -> bool:
        cutoff = time.monotonic() - SECONDS_PER_HOUR
        while self._sent and self._sent[0] <= cutoff:
            self._sent.popleft()
        return len(self._sent) < self._max_per_hour

    async def send(self, text: str) -> bool:
        """Never raises: an alert failing must not take a request down with it."""
        if not self.enabled:
            return False
        if not self._within_budget():
            log.warning("alert suppressed, %d already sent this hour: %s",
                        self._max_per_hour, text.splitlines()[0])
            return False
        self._sent.append(time.monotonic())
        try:
            await self._bot.send_message(self._chat_id, text, parse_mode="HTML")
            return True
        except Exception as error:  # noqa: BLE001
            # The usual cause is the admin never having messaged the bot, so it
            # is not allowed to open a conversation.
            log.warning("could not send alert to %s: %s", self._chat_id, error)
            return False


def new_error_alert(fingerprint: str, platform: str, message: str) -> str:
    return (f"⚠️ <b>new error</b> <code>{fingerprint}</code>\n"
            f"{platform}\n{message[:160]}\n\n"
            f"<code>/errors {fingerprint}</code> for detail")
