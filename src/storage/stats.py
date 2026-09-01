"""What happened, in two shapes.

`events` is one row per request: counts and timings, no URLs and no usernames.
It answers "is this being used and is it working".

`error_signatures` is the unique-error register. The same failure recurring is
one row with a counter; only the *first* occurrence keeps the detail — the raw
error, the URL and the request id that ties it to the Pi log.
"""

import hashlib
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

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
    """SQLite-backed. Disables itself rather than take the bot down with it."""

    def __init__(self, database_path: Path, retention_days: int, salt: str):
        self._lock = threading.Lock()
        self._retention_seconds = max(retention_days, 0) * SECONDS_PER_DAY
        self._salt = salt
        self._connection = self._connect(database_path)

    @staticmethod
    def _connect(database_path: Path) -> sqlite3.Connection | None:
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(database_path, check_same_thread=False)
            connection.executescript(EVENT_LOG_SCHEMA)
            connection.commit()
            return connection
        except (sqlite3.Error, OSError) as error:
            log.warning("event log disabled (%s): %s", database_path, error)
            return None

    def _hash(self, value: int | None) -> str | None:
        """Distinct-but-anonymous: enough to count chats, not to name them."""
        if value is None:
            return None
        digest = hashlib.sha256(f"{self._salt}{value}".encode()).hexdigest()
        return digest[:IDENTIFIER_HASH_LENGTH]

    def record(self, event: Event) -> None:
        if self._connection is None:
            return
        with self._lock:
            self._connection.execute(
                "INSERT INTO events (at, platform, kind, outcome, reason, total_ms,"
                " bytes, reencoded, chat_hash, user_hash, request_id)"
                " VALUES (strftime('%s','now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event.platform, event.kind, event.outcome, event.reason,
                 event.total_ms, event.bytes, int(event.reencoded),
                 self._hash(event.chat_id), self._hash(event.user_id),
                 event.request_id),
            )
            self._connection.commit()

    def record_error(self, *, platform: str, error_type: str, message: str,
                     url: str, detail: str, request_id: str) -> tuple[str, bool]:
        """Upsert the signature. Returns (fingerprint, is_new)."""
        fingerprint = fingerprint_of(platform, error_type, message)
        if self._connection is None:
            return fingerprint, False
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE error_signatures"
                " SET last_seen = strftime('%s','now'), seen_count = seen_count + 1"
                " WHERE fingerprint = ?",
                (fingerprint,),
            )
            is_new = cursor.rowcount == 0
            if is_new:
                # Only the first occurrence keeps detail; repeats are a counter.
                self._connection.execute(
                    "INSERT INTO error_signatures (fingerprint, platform, error_type,"
                    " message, url, detail, request_id, first_seen, last_seen)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'),"
                    " strftime('%s','now'))",
                    (fingerprint, platform, error_type, message[:500], url,
                     (detail or "")[:MAX_STORED_DETAIL_CHARS], request_id),
                )
            self._connection.commit()
        return fingerprint, is_new

    def query(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        if self._connection is None:
            return []
        with self._lock:
            self._connection.row_factory = sqlite3.Row
            return self._connection.execute(sql, parameters).fetchall()

    def counts(self) -> tuple[int, int]:
        events = self.query("SELECT COUNT(*) n FROM events")
        errors = self.query("SELECT COUNT(*) n FROM error_signatures")
        return (events[0]["n"] if events else 0, errors[0]["n"] if errors else 0)

    def prune(self) -> int:
        """Events expire; signatures are kept — they are small and are the history."""
        if self._connection is None or not self._retention_seconds:
            return 0
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM events WHERE at <= strftime('%s','now') - ?",
                (self._retention_seconds,))
            self._connection.commit()
            return cursor.rowcount

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
