from __future__ import annotations

from dataclasses import dataclass
import math
import os

try:
    import psutil
except ImportError:  # pragma: no cover - dependency fallback for source checkouts
    psutil = None


GIB = 1024**3
MIB = 1024**2


@dataclass(frozen=True)
class ResourceSnapshot:
    logical_cpus: int
    total_memory: int
    available_memory: int


class IndexResourcePolicy:
    """Translate user-facing profiles into a bounded document-worker budget."""

    CPU_SHARES = {"gentle": 0.15, "balanced": 0.25, "fast": 0.60}
    MAXIMUM_WORKERS = 20
    MEMORY_PER_WORKER = 256 * MIB

    def snapshot(self) -> ResourceSnapshot:
        cpus = max(1, int(os.cpu_count() or 1))
        if psutil is None:
            return ResourceSnapshot(cpus, 0, 0)
        memory = psutil.virtual_memory()
        return ResourceSnapshot(cpus, int(memory.total), int(memory.available))

    def worker_limit(self, profile: str, snapshot: ResourceSnapshot | None = None) -> int:
        current = snapshot or self.snapshot()
        share = self.CPU_SHARES.get(profile, self.CPU_SHARES["balanced"])
        cpu_limit = max(1, math.floor(current.logical_cpus * share))
        if current.total_memory <= 0:
            memory_limit = self.MAXIMUM_WORKERS
        else:
            reserve = max(2 * GIB, math.ceil(current.total_memory * 0.25))
            memory_limit = max(
                1,
                math.floor((current.available_memory - reserve) / self.MEMORY_PER_WORKER),
            )
        return max(1, min(cpu_limit, memory_limit, self.MAXIMUM_WORKERS))

    def permits_submission(self, active_workers: int) -> bool:
        return active_workers < self.worker_limit("fast", self.snapshot())
