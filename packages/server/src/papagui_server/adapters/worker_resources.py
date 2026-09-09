"""Conservative document concurrency from the process's actual resource limits."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path

from papagui_server.domain.models import ServerSettings

_MIB = 1024 * 1024
_PROFILE_CPU_SHARE = {"gentle": 0.15, "balanced": 0.25, "fast": 0.60}


@dataclass(frozen=True, slots=True)
class WorkerBudget:
    workers: int
    effective_cpus: float
    memory_available_mb: int | None
    memory_reserved_mb: int
    memory_per_worker_mb: int
    reason: str

    @property
    def max_workers(self) -> int:
        return self.workers


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None


def _number(path: Path) -> int | None:
    try:
        value = int(_read(path) or "")
        return value if value >= 0 else None
    except ValueError:
        return None


def _cgroup_directories(controller: str) -> tuple[Path, ...]:
    """Include ancestors: a parent slice may impose the tightest limit.

    Mount roots matter inside containers, where the process cgroup may be
    mounted at /sys/fs/cgroup even though its host path is several levels deep.
    """
    memberships: list[tuple[set[str], str]] = []
    for line in (_read(Path("/proc/self/cgroup")) or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3:
            memberships.append((set(parts[1].split(",")) - {""}, parts[2]))
    directories: list[Path] = []
    for line in (_read(Path("/proc/self/mountinfo")) or "").splitlines():
        before, separator, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        if not separator or len(fields) < 5 or len(filesystem) < 3:
            continue
        if filesystem[0] not in {"cgroup", "cgroup2"}:
            continue
        unified = filesystem[0] == "cgroup2"
        if not unified and controller not in filesystem[2].split(","):
            continue
        mount_root = Path(fields[3].replace("\\040", " "))
        mount_point = Path(fields[4].replace("\\040", " "))
        for controllers, membership in memberships:
            if (unified and controllers) or (not unified and controller not in controllers):
                continue
            try:
                relative = Path(membership).relative_to(mount_root)
            except ValueError:
                # Cgroup namespaces can expose '/' independently of mountinfo.
                if membership != "/":
                    continue
                relative = Path()
            if ".." in relative.parts:
                continue
            current = mount_point / relative
            while True:
                directories.append(current)
                if current == mount_point:
                    break
                current = current.parent
    # Also handle systems where proc metadata is unavailable to this process.
    directories.extend((Path("/sys/fs/cgroup"), Path("/sys/fs/cgroup") / controller))
    if controller == "cpu":
        directories.append(Path("/sys/fs/cgroup/cpu,cpuacct"))
    return tuple(dict.fromkeys(directories))


def _effective_cpus() -> float:
    count = float(os.cpu_count() or 1)
    try:
        count = min(count, float(len(os.sched_getaffinity(0))))
    except (AttributeError, OSError):
        pass
    for directory in _cgroup_directories("cpu"):
        quota = _read(directory / "cpu.max")
        if quota:
            try:
                maximum, period = quota.split()
                if maximum != "max" and int(maximum) > 0 and int(period) > 0:
                    count = min(count, int(maximum) / int(period))
            except ValueError:
                pass
        maximum = _number(directory / "cpu.cfs_quota_us")
        period = _number(directory / "cpu.cfs_period_us")
        if maximum and period:
            count = min(count, maximum / period)
    return max(0.01, count)


def _available_memory_mb() -> int | None:
    available: list[int] = []
    meminfo: dict[str, int] = {}
    for line in (_read(Path("/proc/meminfo")) or "").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            try:
                meminfo[key] = int(value.split()[0]) * 1024
            except (ValueError, IndexError):
                continue
    if "MemAvailable" in meminfo:
        available.append(max(0, meminfo["MemAvailable"]))
    elif "MemFree" in meminfo:
        available.append(
            max(0, sum(meminfo.get(key, 0) for key in ("MemFree", "Buffers", "Cached")))
        )
    for directory in _cgroup_directories("memory"):
        # memory.high also matters: exceeding it can stall every parser thread.
        for limit_name, usage_name in (
            ("memory.max", "memory.current"),
            ("memory.high", "memory.current"),
            ("memory.limit_in_bytes", "memory.usage_in_bytes"),
        ):
            limit, usage = _number(directory / limit_name), _number(directory / usage_name)
            if limit is not None and usage is not None and limit < 1 << 60:
                available.append(max(0, limit - usage))
    return min(available) // _MIB if available else None


def document_worker_budget(settings: ServerSettings) -> WorkerBudget:
    """Apply profile CPU share, a hard cap of 20, and available RAM headroom.

    Each document can run a bounded parser subprocess plus image/text objects in
    the server. Reserve RAM for the server and cache publication before allowing
    additional documents. Unknown or exhausted RAM permits only one worker.
    """
    cpus = _effective_cpus()
    cpu_workers = max(1, min(20, math.floor(cpus * _PROFILE_CPU_SHARE[settings.resource_profile])))
    available = _available_memory_mb()
    reserve = max(512, math.ceil((available or 0) * 0.15))
    overhead = max(
        128,
        math.ceil((settings.image_max_pixels * 12 + settings.max_extracted_characters * 8) / _MIB),
    )
    per_worker = settings.extraction_memory_mb + overhead
    memory_workers = max(1, (available - reserve) // per_worker) if available is not None else 1
    workers = min(cpu_workers, memory_workers)
    reason = (
        "memory_unknown"
        if available is None
        else "memory"
        if memory_workers < cpu_workers
        else "profile_cpu"
    )
    return WorkerBudget(workers, cpus, available, reserve, per_worker, reason)
