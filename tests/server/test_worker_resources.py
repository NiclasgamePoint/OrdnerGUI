from __future__ import annotations

from pathlib import Path

import pytest

from papagui_server.adapters import worker_resources as resources
from papagui_server.domain.models import ServerSettings


@pytest.fixture
def system(monkeypatch):
    files = {"/proc/meminfo": f"MemAvailable: {128 * 1024 * 1024} kB\n"}
    monkeypatch.setattr(resources, "_read", lambda path: files.get(str(path)))
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda _pid: set(range(32)))
    return files


@pytest.mark.parametrize("profile, expected", [("gentle", 4), ("balanced", 8), ("fast", 19)])
def test_profile_uses_effective_logical_cpu_share(system, profile, expected):
    budget = resources.document_worker_budget(ServerSettings(resource_profile=profile))
    assert budget.workers == budget.max_workers == expected
    assert budget.reason == "profile_cpu"


def test_affinity_and_global_twenty_worker_cap(system, monkeypatch):
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 128)
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda _pid: set(range(128)))
    assert resources.document_worker_budget(ServerSettings(resource_profile="fast")).workers == 20
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda _pid: set(range(8)))
    budget = resources.document_worker_budget(ServerSettings(resource_profile="fast"))
    assert budget.effective_cpus == 8
    assert budget.workers == 4


def test_cgroup_v2_ancestor_quota_and_memory_headroom(system):
    system.update(
        {
            "/proc/self/cgroup": "0::/slice/service\n",
            "/proc/self/mountinfo": "20 1 0:20 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n",
            "/sys/fs/cgroup/slice/service/cpu.max": "3200000 100000",
            "/sys/fs/cgroup/slice/cpu.max": "1600000 100000",
            "/sys/fs/cgroup/slice/memory.max": str(8 * 1024**3),
            "/sys/fs/cgroup/slice/memory.current": str(4 * 1024**3),
        }
    )
    budget = resources.document_worker_budget(ServerSettings(resource_profile="fast"))
    assert budget.effective_cpus == 16
    assert budget.memory_available_mb == 4096
    assert budget.memory_reserved_mb >= 512
    assert budget.memory_per_worker_mb > 768
    assert budget.workers == 3
    assert budget.reason == "memory"


def test_cgroup_v1_mount_root_and_fractional_quota(system):
    system.update(
        {
            "/proc/self/cgroup": "2:cpu,cpuacct:/docker/example/app\n3:memory:/docker/example/app\n",
            "/proc/self/mountinfo": (
                "20 1 0:20 /docker/example /sys/fs/cgroup/cpu rw - cgroup cgroup rw,cpu,cpuacct\n"
                "21 1 0:21 /docker/example /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory\n"
            ),
            "/sys/fs/cgroup/cpu/app/cpu.cfs_quota_us": "250000",
            "/sys/fs/cgroup/cpu/app/cpu.cfs_period_us": "100000",
            "/sys/fs/cgroup/memory/app/memory.limit_in_bytes": str(2 * 1024**3),
            "/sys/fs/cgroup/memory/app/memory.usage_in_bytes": str(1024**3),
        }
    )
    budget = resources.document_worker_budget(ServerSettings(resource_profile="fast"))
    assert budget.effective_cpus == 2.5
    assert budget.memory_available_mb == 1024
    assert budget.workers == 1


def test_unlimited_and_invalid_cgroup_values_do_not_hide_real_limits(system):
    system.update(
        {
            "/sys/fs/cgroup/cpu.max": "max 100000",
            "/sys/fs/cgroup/cpu.cfs_quota_us": "-1",
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/memory.current": "10",
            "/sys/fs/cgroup/memory.high": "invalid",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(2**63 - 4096),
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": "0",
        }
    )
    budget = resources.document_worker_budget(ServerSettings())
    assert budget.workers == 8
    assert budget.memory_available_mb == 128 * 1024


def test_memory_high_and_current_usage_bound_workers(system):
    system.update(
        {
            "/sys/fs/cgroup/memory.max": str(32 * 1024**3),
            "/sys/fs/cgroup/memory.high": str(4 * 1024**3),
            "/sys/fs/cgroup/memory.current": str(1024**3),
        }
    )
    budget = resources.document_worker_budget(ServerSettings(resource_profile="fast"))
    assert budget.memory_available_mb == 3072
    assert budget.workers == 2


@pytest.mark.parametrize("memory", [None, "MemAvailable: 0 kB", "MemAvailable: 256 kB"])
def test_unknown_or_exhausted_ram_keeps_one_worker(system, memory):
    system["/proc/meminfo"] = memory
    assert resources.document_worker_budget(ServerSettings(resource_profile="fast")).workers == 1


def test_larger_parser_budget_reduces_concurrency(system):
    system["/proc/meminfo"] = f"MemAvailable: {8 * 1024 * 1024} kB\n"
    small = resources.document_worker_budget(
        ServerSettings(resource_profile="fast", extraction_memory_mb=128)
    )
    large = resources.document_worker_budget(
        ServerSettings(resource_profile="fast", extraction_memory_mb=2048)
    )
    assert small.workers > large.workers
    assert large.workers * large.memory_per_worker_mb + large.memory_reserved_mb <= 8192


def test_unreadable_system_file_is_unknown(tmp_path: Path):
    assert resources._read(tmp_path / "missing") is None
