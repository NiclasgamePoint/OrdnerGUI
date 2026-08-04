from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IndexLayout:
    """Resolve every path owned by the split index without global path arithmetic."""

    root: Path

    @property
    def catalog_dir(self) -> Path:
        return self.root / "catalog"

    @property
    def catalog_path(self) -> Path:
        return self.catalog_dir / "active.db"

    @property
    def catalog_build_dir(self) -> Path:
        return self.catalog_dir / "builds"

    @property
    def catalog_backup_dir(self) -> Path:
        return self.catalog_dir / "backups"

    @property
    def content_dir(self) -> Path:
        return self.root / "content"

    @property
    def content_state_path(self) -> Path:
        return self.content_dir / "state.db"

    @property
    def shard_dir(self) -> Path:
        return self.content_dir / "shards"

    @property
    def corrupt_shard_dir(self) -> Path:
        return self.content_dir / "corrupt"

    @property
    def jobs_dir(self) -> Path:
        return self.root / "jobs"

    @property
    def legacy_dir(self) -> Path:
        return self.root / "legacy-v0.2"

    def ensure_directories(self):
        for path in (
            self.catalog_dir,
            self.catalog_build_dir,
            self.catalog_backup_dir,
            self.content_dir,
            self.shard_dir,
            self.corrupt_shard_dir,
            self.jobs_dir,
            self.legacy_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def shard_path(self, name: str) -> Path:
        if not name or Path(name).name != name or not name.endswith(".db"):
            raise ValueError(f"Ungültiger Shard-Name: {name!r}")
        return self.shard_dir / name
