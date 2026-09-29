"""The storage connection: local or Turso, and never fatal."""

import os
import sys

import pytest

from src import app
from src.core.config import Config
from src.storage.cache import FileIdCache
from src.storage.database import Database
from src.storage.stats import EventLog


class BrokenConnection:
    """A connection whose every call fails, as a remote one does in an outage."""

    def execute(self, *args):
        raise ValueError("Hrana: stream error")

    def executescript(self, *args):
        raise ValueError("Hrana: stream error")

    def commit(self):
        raise ValueError("Hrana: stream error")

    def close(self):
        raise ValueError("Hrana: stream error")


@pytest.fixture
def database(tmp_path):
    return Database.local(tmp_path / "db.sqlite")


def test_rows_come_back_as_dicts(database):
    database.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    database.execute("INSERT INTO t VALUES (?, ?)", (1, "x"))
    assert database.query("SELECT a, b FROM t") == [{"a": 1, "b": "x"}]


def test_writes_report_rows_affected(database):
    database.execute("CREATE TABLE t (a INTEGER)")
    assert database.execute("INSERT INTO t VALUES (1)") == 1
    assert database.execute("DELETE FROM t WHERE a = 2") == 0


class TestOutage:
    @pytest.fixture
    def broken(self):
        return Database(BrokenConnection(), "libsql://down")

    def test_failures_are_answered_with_nothing(self, broken):
        assert broken.execute("INSERT INTO t VALUES (1)") is None
        assert broken.query("SELECT 1") == []
        assert broken.create("CREATE TABLE t (a INTEGER)") is False
        broken.close()
        broken.close()

    def test_a_failed_write_is_not_a_new_error(self, tmp_path):
        """Otherwise every failure during an outage would alert as new."""
        events = EventLog(Database.local(tmp_path / "e.db"), 90, "salt")
        events._database = Database(BrokenConnection(), "libsql://down")
        _, is_new = events.record_error(
            platform="instagram", error_type="ClipUnavailable", message="boom",
            url="https://instagram.com/p/X", detail="", request_id="r1")
        assert is_new is False

    def test_storage_that_cannot_start_disables_itself(self):
        broken = Database(BrokenConnection(), "libsql://down")
        cache = FileIdCache(broken, 30)
        cache.put("k", "file")
        assert cache.get("k") is None


class TestTurso:
    def test_missing_client_disables_it(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "libsql", None)
        assert Database.turso("libsql://x.turso.io", "token") is None

    def test_the_libsql_client_runs_our_schema(self):
        """libsql speaks the same SQL; ':memory:' exercises it without a server."""
        pytest.importorskip("libsql")
        database = Database.turso(":memory:", "")
        events = EventLog(database, 90, "salt")
        fingerprint, is_new = events.record_error(
            platform="x", error_type="E", message="m", url="u", detail="d",
            request_id="r")
        assert is_new is True
        assert events.record_error(platform="x", error_type="E", message="m",
                                   url="u", detail="d", request_id="r") == (
            fingerprint, False)
        cache = FileIdCache(database, 30)
        cache.put("k", "file")
        assert cache.get("k") == "file"

    @pytest.mark.network
    def test_a_real_turso_database(self):
        """Needs TURSO_TEST_URL and TURSO_TEST_TOKEN: a throwaway database."""
        url = os.environ.get("TURSO_TEST_URL")
        if not url:
            pytest.skip("TURSO_TEST_URL not set")
        database = Database.turso(url, os.environ.get("TURSO_TEST_TOKEN", ""))
        assert database is not None
        cache = FileIdCache(database, 30)
        cache.put("test:key", "file")
        assert cache.get("test:key") == "file"


class TestChoosingStorage:
    def test_local_files_without_turso(self, tmp_path):
        cache, events = app._open_databases(Config(bot_token="x", data_dir=tmp_path))
        assert cache.label.endswith("sent_clips.db")
        assert events.label.endswith("events.db")

    def test_turso_holds_both(self, tmp_path, monkeypatch):
        shared = Database.local(tmp_path / "remote.db")
        monkeypatch.setattr(Database, "turso", classmethod(lambda cls, url, token: shared))
        config = Config(bot_token="x", data_dir=tmp_path,
                        turso_database_url="libsql://x.turso.io")
        assert app._open_databases(config) == (shared, shared)

    def test_unreachable_turso_falls_back_to_local(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Database, "turso", classmethod(lambda cls, url, token: None))
        config = Config(bot_token="x", data_dir=tmp_path,
                        turso_database_url="libsql://x.turso.io")
        cache, _ = app._open_databases(config)
        assert cache.label.endswith("sent_clips.db")
