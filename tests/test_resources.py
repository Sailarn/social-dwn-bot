"""Refusing work the machine cannot afford."""

import pytest

from src.core import resources
from src.core.errors import ClipUnavailable


class TestDisk:
    def test_plenty_of_space_passes(self, tmp_path):
        resources.ensure_disk(tmp_path, minimum_mb=1)

    def test_low_space_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resources, "free_disk_bytes", lambda path: 10 * 1024 * 1024)
        with pytest.raises(ClipUnavailable) as caught:
            resources.ensure_disk(tmp_path, minimum_mb=500)
        assert caught.value.reason == "low_disk"

    def test_zero_disables_the_check(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resources, "free_disk_bytes", lambda path: 0)
        resources.ensure_disk(tmp_path, minimum_mb=0)


class TestMemory:
    def test_plenty_passes(self, monkeypatch):
        monkeypatch.setattr(resources, "available_memory_bytes",
                            lambda: 2 * 1024 ** 3)
        resources.ensure_memory(minimum_mb=200)

    def test_low_memory_is_refused(self, monkeypatch):
        monkeypatch.setattr(resources, "available_memory_bytes",
                            lambda: 50 * 1024 * 1024)
        with pytest.raises(ClipUnavailable) as caught:
            resources.ensure_memory(minimum_mb=200)
        assert caught.value.reason == "low_memory"

    def test_unknown_memory_skips_the_check(self, monkeypatch):
        """Not measurable off Linux; guessing would be worse than not checking."""
        monkeypatch.setattr(resources, "available_memory_bytes", lambda: None)
        resources.ensure_memory(minimum_mb=200)

    def test_zero_disables_the_check(self, monkeypatch):
        monkeypatch.setattr(resources, "available_memory_bytes", lambda: 1)
        resources.ensure_memory(minimum_mb=0)

    def test_meminfo_is_parsed_when_present(self, tmp_path, monkeypatch):
        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemTotal:  3999999 kB\nMemAvailable:  2097152 kB\n")
        monkeypatch.setattr(resources, "MEMINFO", meminfo)
        assert resources.available_memory_bytes() == 2097152 * 1024

    def test_a_missing_meminfo_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resources, "MEMINFO", tmp_path / "nope")
        assert resources.available_memory_bytes() is None
