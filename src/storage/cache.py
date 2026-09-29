"""Maps already-sent media to the file_id Telegram gave us.

A cache hit lets us re-send without downloading or uploading anything, which is
the difference between instant and a few seconds, and between spending bandwidth
and spending none. Entries expire so the bot stops serving posts that have since
been deleted, and so the database cannot grow without bound.
"""

import logging

from src.storage.database import Database

log = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_clips (
    clip_key   TEXT PRIMARY KEY,
    file_id    TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
"""


class FileIdCache:
    """Disables itself rather than crash the bot: no database, no cache."""

    def __init__(self, database: Database | None, ttl_days: int):
        # A non-positive TTL means entries never expire.
        self._ttl_seconds = ttl_days * SECONDS_PER_DAY if ttl_days > 0 else None
        self._database = database if database and database.create(_SCHEMA) else None
        if database is not None and self._database is None:
            log.warning("file_id cache disabled")

    def get(self, clip_key: str) -> str | None:
        if self._database is None:
            return None
        query = "SELECT file_id FROM sent_clips WHERE clip_key = ?"
        parameters: tuple = (clip_key,)
        if self._ttl_seconds is not None:
            query += " AND created_at > strftime('%s', 'now') - ?"
            parameters += (self._ttl_seconds,)
        rows = self._database.query(query, parameters)
        return rows[0]["file_id"] if rows else None

    def put(self, clip_key: str, file_id: str) -> None:
        if self._database is None:
            return
        self._database.execute(
            "INSERT OR REPLACE INTO sent_clips (clip_key, file_id, created_at)"
            " VALUES (?, ?, strftime('%s', 'now'))",
            (clip_key, file_id),
        )

    def prune(self) -> int:
        """Delete expired rows. Returns how many went."""
        if self._database is None or self._ttl_seconds is None:
            return 0
        deleted = self._database.execute(
            "DELETE FROM sent_clips WHERE created_at <= strftime('%s', 'now') - ?",
            (self._ttl_seconds,),
        )
        return deleted or 0

    def close(self) -> None:
        if self._database is not None:
            self._database.close()
