"""What happened, in two shapes.

`events` is one row per request: counts and timings, no URLs and no usernames.
It answers "is this being used and is it working".

`error_signatures` is the unique-error register. The same failure recurring is
one row with a counter; only the *first* occurrence keeps the detail — the raw
error, the URL and the request id that ties it to the host's log.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from src.storage.database import Database
from src.storage.schema import EVENT_LOG_SCHEMA

log = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400
FINGERPRINT_LENGTH = 6
IDENTIFIER_HASH_LENGTH = 12
MAX_STORED_DETAIL_CHARS = 4000

# Anything that varies between occurrences of the same underlying fault, so the
# fingerprint groups them instead of creating a row per incident. The middle
# branch catches opaque post ids such as `Cx1y2z3AbCd`: a long token mixing
# letters and digits. Without it one broken extractor becomes a row per post.
_VOLATILE = re.compile(
    r"https?://\S+"
    r"|\b(?=[\w-]*\d)(?=[\w-]*[a-z])[\w-]{8,}\b"
    r"|\b\d+\b",
    re.IGNORECASE,
)



@dataclass(frozen=True)
class Event:
    outcome: str
    platform: str | None = None
    kind: str | None = None
    reason: str | None = None
    total_ms: int = 0
    bytes: int = 0
    reencoded: bool = False
    chat_id: int | None = None
    user_id: int | None = None
    request_id: str | None = None


def fingerprint_of(platform: str, error_type: str, message: str) -> str:
    """Group the same fault together regardless of which post triggered it."""
    stable = _VOLATILE.sub("#", (message or "").lower())
    digest = hashlib.sha1(f"{platform}|{error_type}|{stable}".encode()).hexdigest()
    return digest[:FINGERPRINT_LENGTH]


class EventLog:
    """Disables itself rather than take the bot down with it."""

    def __init__(self, database: Database | None, retention_days: int, salt: str):
        self._retention_seconds = max(retention_days, 0) * SECONDS_PER_DAY
        self._salt = salt
        self._database = (database if database and database.create(EVENT_LOG_SCHEMA)
                          else None)
        if database is not None and self._database is None:
            log.warning("event log disabled")

    def _hash(self, value: int | None) -> str | None:
        """Distinct-but-anonymous: enough to count chats, not to name them."""
        if value is None:
            return None
        digest = hashlib.sha256(f"{self._salt}{value}".encode()).hexdigest()
        return digest[:IDENTIFIER_HASH_LENGTH]

    def record(self, event: Event) -> None:
        if self._database is None:
            return
        self._database.execute(
            "INSERT INTO events (at, platform, kind, outcome, reason, total_ms,"
            " bytes, reencoded, chat_hash, user_hash, request_id)"
            " VALUES (strftime('%s','now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event.platform, event.kind, event.outcome, event.reason,
             event.total_ms, event.bytes, int(event.reencoded),
             self._hash(event.chat_id), self._hash(event.user_id),
             event.request_id),
        )

    def record_error(self, *, platform: str, error_type: str, message: str,
                     url: str, detail: str, request_id: str) -> tuple[str, bool]:
        """Upsert the signature. Returns (fingerprint, is_new)."""
        fingerprint = fingerprint_of(platform, error_type, message)
        if self._database is None:
            return fingerprint, False
        updated = self._database.execute(
            "UPDATE error_signatures"
            " SET last_seen = strftime('%s','now'), seen_count = seen_count + 1"
            " WHERE fingerprint = ?",
            (fingerprint,),
        )
        # A failed write is not a new error: alerting on every failure during
        # a storage outage would bury the one alert that matters.
        if updated != 0:
            return fingerprint, False
        # Only the first occurrence keeps detail; repeats are a counter.
        inserted = self._database.execute(
            "INSERT INTO error_signatures (fingerprint, platform, error_type,"
            " message, url, detail, request_id, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'),"
            " strftime('%s','now'))",
            (fingerprint, platform, error_type, message[:500], url,
             (detail or "")[:MAX_STORED_DETAIL_CHARS], request_id),
        )
        return fingerprint, inserted == 1

    def query(self, sql: str, parameters: tuple = ()) -> list[dict]:
        if self._database is None:
            return []
        return self._database.query(sql, parameters)

    def counts(self) -> tuple[int, int]:
        events = self.query("SELECT COUNT(*) n FROM events")
        errors = self.query("SELECT COUNT(*) n FROM error_signatures")
        return (events[0]["n"] if events else 0, errors[0]["n"] if errors else 0)

    def prune(self) -> int:
        """Events expire; signatures are kept — they are small and are the history."""
        if self._database is None or not self._retention_seconds:
            return 0
        deleted = self._database.execute(
            "DELETE FROM events WHERE at <= strftime('%s','now') - ?",
            (self._retention_seconds,))
        return deleted or 0

    def close(self) -> None:
        if self._database is not None:
            self._database.close()
