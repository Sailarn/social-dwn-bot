"""Maps already-sent media to the file_id Telegram gave us.

A cache hit lets us re-send without downloading or uploading anything, which is
the difference between instant and a few seconds, and between spending bandwidth
and spending none. Entries expire so the bot stops serving posts that have since
been deleted, and so the database cannot grow without bound.
"""

import logging
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_clips (
    clip_key   TEXT PRIMARY KEY,
    file_id    TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
)
"""


class FileIdCache:
    """SQLite-backed cache that disables itself rather than crash the bot."""

    def __init__(self, database_path: Path, ttl_days: int):
        self._lock = threading.Lock()
        # A non-positive TTL means entries never expire.
        self._ttl_seconds = ttl_days * SECONDS_PER_DAY if ttl_days > 0 else None
        self._connection = self._connect(database_path)

    @staticmethod
    def _connect(database_path: Path) -> sqlite3.Connection | None:
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(database_path, check_same_thread=False)
            connection.execute(_SCHEMA)
            connection.commit()
            return connection
        except (sqlite3.Error, OSError) as error:
            log.warning("file_id cache disabled (%s): %s", database_path, error)
            return None

    def get(self, clip_key: str) -> str | None:
        if self._connection is None:
            return None
        query = "SELECT file_id FROM sent_clips WHERE clip_key = ?"
        parameters: tuple = (clip_key,)
        if self._ttl_seconds is not None:
            query += " AND created_at > strftime('%s', 'now') - ?"
            parameters += (self._ttl_seconds,)
        with self._lock:
            row = self._connection.execute(query, parameters).fetchone()
        return row[0] if row else None

    def put(self, clip_key: str, file_id: str) -> None:
        if self._connection is None:
            return
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO sent_clips (clip_key, file_id, created_at)"
                " VALUES (?, ?, strftime('%s', 'now'))",
                (clip_key, file_id),
            )
            self._connection.commit()

    def prune(self) -> int:
        """Delete expired rows. Returns how many went."""
        if self._connection is None or self._ttl_seconds is None:
            return 0
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM sent_clips"
                " WHERE created_at <= strftime('%s', 'now') - ?",
                (self._ttl_seconds,),
            )
            self._connection.commit()
            return cursor.rowcount

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
