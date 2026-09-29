"""Reading the event log back: the numbers behind /stats and /errors."""

from src.storage.stats import SECONDS_PER_DAY, EventLog


def _percentile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


SERVED = ("sent", "cache_hit")
STEP_ORDER = ("cache", "anonymous", "apify", "cookies")


def _step_order(entry: dict) -> int:
    step = entry["step"]
    return STEP_ORDER.index(step) if step in STEP_ORDER else len(STEP_ORDER)


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

    def sources(self, days: int) -> list[dict]:
        """Per step: how often it was tried, how often it served the post, and
        its median time when it did. Cache hits have no trail of their own."""
        rows = self._events.query(
            "SELECT tried, source, outcome, total_ms FROM events"
            " WHERE at > strftime('%s','now') - ?"
            " AND (tried IS NOT NULL OR source IS NOT NULL)",
            (int(days) * SECONDS_PER_DAY,))
        by_step: dict[str, dict] = {}
        for row in rows:
            steps = (row["tried"] or "").split(",") if row["tried"] else []
            if row["source"] and row["source"] not in steps:
                steps.append(row["source"])
            for step in steps:
                entry = by_step.setdefault(step, {"step": step, "tried": 0,
                                                  "served": 0, "times": []})
                entry["tried"] += 1
                if step == row["source"] and row["outcome"] in SERVED:
                    entry["served"] += 1
                    entry["times"].append(row["total_ms"] or 0)
        return [
            {"step": entry["step"], "tried": entry["tried"], "served": entry["served"],
             "median_ms": _percentile(sorted(entry["times"]), 0.50)}
            for entry in sorted(by_step.values(), key=_step_order)
        ]

    def platforms(self, days: int) -> list[dict]:
        rows = self._events.query(
            "SELECT platform, outcome, COUNT(*) n FROM events"
            " WHERE at > strftime('%s','now') - ? AND platform IS NOT NULL"
            " GROUP BY platform, outcome",
            (int(days) * SECONDS_PER_DAY,))
        totals: dict[str, dict] = {}
        for row in rows:
            entry = totals.setdefault(row["platform"], {"platform": row["platform"],
                                                        "requests": 0, "served": 0})
            entry["requests"] += row["n"]
            if row["outcome"] in SERVED:
                entry["served"] += row["n"]
        return sorted(totals.values(), key=lambda entry: -entry["requests"])

    def chats(self, days: int) -> dict:
        """Groups by name; private chats only as a total, never named."""
        rows = self._events.query(
            "SELECT c.title, c.chat_id, COUNT(*) n FROM events e"
            " LEFT JOIN chats c ON c.chat_hash = e.chat_hash"
            " WHERE e.at > strftime('%s','now') - ? AND e.chat_hash IS NOT NULL"
            " GROUP BY e.chat_hash ORDER BY n DESC",
            (int(days) * SECONDS_PER_DAY,))
        groups = [row for row in rows if row["title"] is not None]
        private = [row for row in rows if row["title"] is None]
        return {
            "groups": [(row["title"], row["chat_id"], row["n"]) for row in groups],
            "private_chats": len(private),
            "private_requests": sum(row["n"] for row in private),
        }

    def since(self, started_at: int) -> dict:
        """Budgets since a point in time: the start of the billing month."""
        rows = self._events.query(
            "SELECT"
            " SUM(CASE WHEN tried LIKE '%apify%' THEN 1 ELSE 0 END) apify_runs,"
            " SUM(CASE WHEN tried LIKE '%cookies%' THEN 1 ELSE 0 END) cookie_uses,"
            " SUM(COALESCE(bytes, 0)) sent_bytes"
            " FROM events WHERE at >= ?",
            (int(started_at),))
        row = rows[0] if rows else {}
        return {key: row.get(key) or 0
                for key in ("apify_runs", "cookie_uses", "sent_bytes")}

    def recent_errors(self, limit: int = 10) -> list[dict]:
        return self._events.query(
            "SELECT * FROM error_signatures ORDER BY last_seen DESC LIMIT ?", (limit,))

    def error_detail(self, fingerprint: str) -> dict | None:
        rows = self._events.query(
            "SELECT * FROM error_signatures WHERE fingerprint = ?", (fingerprint,))
        return rows[0] if rows else None
