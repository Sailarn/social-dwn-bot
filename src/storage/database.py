"""One connection to wherever the bot keeps its state.

Locally that is a SQLite file. On a host whose disk is wiped on every deploy
(Render), it is a Turso database — SQLite over the network — so the re-send
cache and the stats survive. The SQL is the same for both.

Storage is a convenience, never a dependency: a failed query is logged and
answered with "nothing", so a database outage costs cache hits and stats, not
deliveries.
"""

import contextlib
import logging
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# sqlite3 raises its own errors; libsql raises ValueError for everything,
# network failures included.
STORAGE_ERRORS = (sqlite3.Error, OSError, ValueError)


class Database:
    """Thread-safe, dict rows, and failures become warnings.

    Calls block: from async code, run them in a worker thread. A remote query
    that hangs must not freeze polling.
    """

    def __init__(self, connection, label: str):
        self._connection = connection
        self._label = label
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def local(cls, path: Path) -> "Database | None":
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, check_same_thread=False)
        except STORAGE_ERRORS as error:
            log.warning("storage disabled (%s): %s", path, error)
            return None
        return cls(connection, str(path))

    @classmethod
    def turso(cls, url: str, auth_token: str) -> "Database | None":
        """Remote only: every query goes to Turso, nothing is kept on disk."""
        try:
            # Imported here: only Turso hosts need it, and it has no wheel for
            # the Pi's ARM Python.
            import libsql

            connection = libsql.connect(database=url, auth_token=auth_token)
            # Connecting is lazy; a bad URL or token only shows on first use.
            connection.execute("SELECT 1").fetchall()
        except ImportError:
            log.warning("TURSO_DATABASE_URL is set but the libsql package is "
                        "not installed")
            return None
        except STORAGE_ERRORS as error:
            log.warning("could not reach Turso at %s: %s", url, error)
            return None
        return cls(connection, url)

    @property
    def label(self) -> str:
        return self._label

    def create(self, schema: str) -> bool:
        """Run CREATE ... IF NOT EXISTS statements. False if it failed."""
        with self._lock:
            try:
                self._connection.executescript(schema)
                self._connection.commit()
                return True
            except STORAGE_ERRORS as error:
                log.warning("could not create tables in %s: %s", self._label, error)
                return False

    def execute(self, sql: str, parameters: tuple = ()) -> int | None:
        """Run a write and commit it. Rows affected, or None if it failed."""
        with self._lock:
            try:
                cursor = self._connection.execute(sql, parameters)
                self._connection.commit()
                return cursor.rowcount
            except STORAGE_ERRORS as error:
                log.warning("storage write failed (%s): %s", self._label, error)
                return None

    def query(self, sql: str, parameters: tuple = ()) -> list[dict]:
        with self._lock:
            try:
                cursor = self._connection.execute(sql, parameters)
                columns = [column[0] for column in cursor.description or ()]
                return [dict(zip(columns, row, strict=True))
                        for row in cursor.fetchall()]
            except STORAGE_ERRORS as error:
                log.warning("storage read failed (%s): %s", self._label, error)
                return []

    def close(self) -> None:
        """Idempotent: the cache and the event log may share one database."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            with contextlib.suppress(*STORAGE_ERRORS):
                self._connection.close()
