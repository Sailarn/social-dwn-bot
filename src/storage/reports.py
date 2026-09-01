"""Reading the event log back: the numbers behind /stats and /errors."""

import sqlite3

from src.storage.stats import SECONDS_PER_DAY, EventLog


def _percentile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


class Reports:
    """Queries only. Writing lives in EventLog."""

    def __init__(self, events: EventLog):
        self._events = events

    def summary(self, days: int) -> dict:
        since = f"strftime('%s','now') - {int(days) * SECONDS_PER_DAY}"
        rows = self._events.query(
            f"SELECT outcome, COUNT(*) n FROM events WHERE at > {since} GROUP BY outcome")
        counts = {row["outcome"]: row["n"] for row in rows}
        reasons = self._events.query(
            f"SELECT reason, COUNT(*) n FROM events"
            f" WHERE at > {since} AND reason IS NOT NULL"
            f" GROUP BY reason ORDER BY n DESC LIMIT 5")
        chats = self._events.query(
            f"SELECT COUNT(DISTINCT chat_hash) n FROM events WHERE at > {since}")
        timings = self._events.query(
            f"SELECT total_ms FROM events"
            f" WHERE at > {since} AND outcome = 'sent' AND total_ms > 0"
            f" ORDER BY total_ms")
        durations = [row["total_ms"] for row in timings]
        return {
            "counts": counts,
            "total": sum(counts.values()),
            "reasons": [(row["reason"], row["n"]) for row in reasons],
            "chats": chats[0]["n"] if chats else 0,
            "median_ms": _percentile(durations, 0.50),
            "p95_ms": _percentile(durations, 0.95),
        }

    def busiest_chats(self, days: int, limit: int = 3) -> list[tuple[str, int]]:
        since = f"strftime('%s','now') - {int(days) * SECONDS_PER_DAY}"
        rows = self._events.query(
            f"SELECT chat_hash, COUNT(*) n FROM events"
            f" WHERE at > {since} AND chat_hash IS NOT NULL"
            f" GROUP BY chat_hash ORDER BY n DESC LIMIT {int(limit)}")
        return [(row["chat_hash"], row["n"]) for row in rows]

    def recent_errors(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._events.query(
            "SELECT * FROM error_signatures ORDER BY last_seen DESC LIMIT ?", (limit,))

    def error_detail(self, fingerprint: str) -> sqlite3.Row | None:
        rows = self._events.query(
            "SELECT * FROM error_signatures WHERE fingerprint = ?", (fingerprint,))
        return rows[0] if rows else None
