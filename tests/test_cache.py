"""file_id cache: expiry, refresh, and rows written before photos existed."""

import sqlite3
from pathlib import Path

import pytest

from src.storage.cache import FileIdCache

DAY = 86400


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "cache.db"


def backdate(db_path, key, days):
    raw = sqlite3.connect(db_path)
    raw.execute(
        "INSERT OR REPLACE INTO sent_clips (clip_key, file_id, created_at)"
        " VALUES (?, ?, strftime('%s','now') - ?)",
        (key, f"video|{key}", days * DAY),
    )
    raw.commit()
    raw.close()


def test_round_trip(db_path):
    cache = FileIdCache(db_path, ttl_days=30)
    assert cache.get("missing") is None
    cache.put("k", "video|FILE")
    assert cache.get("k") == "video|FILE"
    cache.put("k", "video|NEWER")
    assert cache.get("k") == "video|NEWER"


def test_expired_rows_are_not_returned(db_path):
    cache = FileIdCache(db_path, ttl_days=30)
    backdate(db_path, "stale", days=31)
    assert cache.get("stale") is None


def test_rows_inside_the_window_survive(db_path):
    cache = FileIdCache(db_path, ttl_days=30)
    backdate(db_path, "fresh", days=29)
    assert cache.get("fresh") == "video|fresh"


def test_prune_deletes_only_expired(db_path):
    cache = FileIdCache(db_path, ttl_days=30)
    cache.put("keep", "video|KEEP")
    backdate(db_path, "drop", days=31)
    assert cache.prune() == 1
    assert cache.get("keep") == "video|KEEP"


def test_resending_refreshes_the_timestamp(db_path):
    """A link still in use must not expire out from under it."""
    cache = FileIdCache(db_path, ttl_days=30)
    backdate(db_path, "old", days=31)
    assert cache.get("old") is None
    cache.put("old", "video|REVIVED")
    assert cache.get("old") == "video|REVIVED"


def test_ttl_zero_means_never_expire(db_path):
    cache = FileIdCache(db_path, ttl_days=0)
    backdate(db_path, "ancient", days=3650)
    assert cache.get("ancient") == "video|ancient"
    assert cache.prune() == 0


def test_unwritable_path_disables_the_cache_instead_of_crashing():
    cache = FileIdCache(Path("/proc/nope/cache.db"), ttl_days=30)
    assert cache.get("anything") is None
    cache.put("anything", "video|X")   # must not raise
    assert cache.prune() == 0

