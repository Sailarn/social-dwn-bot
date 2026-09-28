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
        monkeypatch.setattr(resources, "CGROUP_MEMORY_FILES", ())
        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemTotal:  3999999 kB\nMemAvailable:  2097152 kB\n")
        monkeypatch.setattr(resources, "MEMINFO", meminfo)
        assert resources.available_memory_bytes() == 2097152 * 1024

    def test_a_missing_meminfo_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resources, "CGROUP_MEMORY_FILES", ())
        monkeypatch.setattr(resources, "MEMINFO", tmp_path / "nope")
        assert resources.available_memory_bytes() is None


MB = 1024 * 1024


class TestContainerMemory:
    """Inside a container /proc/meminfo shows the host; the cgroup shows the cap."""

    @pytest.fixture
    def cgroup(self, tmp_path, monkeypatch):
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(f"MemAvailable:  {16 * 1024 * 1024} kB\n")  # a 16 GB host
        monkeypatch.setattr(resources, "MEMINFO", meminfo)
        files = (tmp_path / "memory.max", tmp_path / "memory.current",
                 tmp_path / "memory.stat", "inactive_file")
        monkeypatch.setattr(resources, "CGROUP_MEMORY_FILES", (files,))

        def write(limit, usage, inactive_file=0):
            files[0].write_text(f"{limit}\n")
            files[1].write_text(f"{usage}\n")
            files[2].write_text(f"anon 1\ninactive_file {inactive_file}\n")
        return write

    def test_the_container_cap_wins_over_the_host(self, cgroup):
        cgroup(limit=512 * MB, usage=400 * MB)
        assert resources.available_memory_bytes() == 112 * MB

    def test_reclaimable_page_cache_is_not_counted_as_used(self, cgroup):
        cgroup(limit=512 * MB, usage=400 * MB, inactive_file=100 * MB)
        assert resources.available_memory_bytes() == 212 * MB

    def test_an_uncapped_container_falls_back_to_the_host(self, cgroup):
        cgroup(limit="max", usage=400 * MB)
        assert resources.available_memory_bytes() == 16 * 1024 * MB

    def test_a_v1_no_limit_value_counts_as_uncapped(self, cgroup):
        cgroup(limit=9223372036854771712, usage=400 * MB)
        assert resources.available_memory_bytes() == 16 * 1024 * MB

    def test_over_the_cap_is_zero_not_negative(self, cgroup):
        cgroup(limit=512 * MB, usage=600 * MB)
        assert resources.available_memory_bytes() == 0

    def test_a_near_cap_container_is_refused(self, cgroup):
        cgroup(limit=512 * MB, usage=450 * MB)
        with pytest.raises(ClipUnavailable):
            resources.ensure_memory(minimum_mb=100)
