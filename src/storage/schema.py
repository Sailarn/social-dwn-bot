"""Table definitions for the event log."""

EVENT_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    at          INTEGER NOT NULL,
    platform    TEXT,
    kind        TEXT,
    outcome     TEXT NOT NULL,
    reason      TEXT,
    total_ms    INTEGER,
    bytes       INTEGER,
    reencoded   INTEGER DEFAULT 0,
    chat_hash   TEXT,
    user_hash   TEXT,
    request_id  TEXT,
    source      TEXT,
    tried       TEXT
);
CREATE INDEX IF NOT EXISTS events_at ON events (at);

-- Groups only, so the admin's stats can name them. Private chats are never
-- stored here: they stay a hash in `events`.
CREATE TABLE IF NOT EXISTS chats (
    chat_hash   TEXT PRIMARY KEY,
    chat_id     INTEGER NOT NULL,
    title       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS error_signatures (
    fingerprint TEXT PRIMARY KEY,
    platform    TEXT,
    error_type  TEXT,
    message     TEXT,
    url         TEXT,
    detail      TEXT,
    request_id  TEXT,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    seen_count  INTEGER NOT NULL DEFAULT 1
);
"""

# Columns added after the first release, for event logs that predate them.
# (table, column, type)
ADDED_COLUMNS = (
    ("events", "source", "TEXT"),
    ("events", "tried", "TEXT"),
)
