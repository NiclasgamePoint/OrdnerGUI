"""Translate portable source paths into platform-specific local paths."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
import sys
from typing import Iterable, Mapping

from papagui_contracts.generations import SourcePath


class PlatformFamily(StrEnum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"

    @classmethod
    def current(cls) -> PlatformFamily:
        if os.name == "nt" or sys.platform.startswith("win"):
            return cls.WINDOWS
        if sys.platform == "darwin":
            return cls.MACOS
        return cls.LINUX


@dataclass(frozen=True, slots=True)
class SourceMapping:
    source_id: str
    windows: str | None = None
    macos: str | None = None
    linux: str | None = None

    def root_for(self, platform: PlatformFamily) -> str | None:
        return {
            PlatformFamily.WINDOWS: self.windows,
            PlatformFamily.MACOS: self.macos,
            PlatformFamily.LINUX: self.linux,
        }[platform]

    def to_dict(self) -> dict[str, str]:
        return {
            name: value
            for name, value in (
                ("windows", self.windows),
                ("macos", self.macos),
                ("linux", self.linux),
            )
            if value is not None
        }

    @classmethod
    def from_dict(cls, source_id: str, values: Mapping[str, object]) -> SourceMapping:
        def optional(name: str) -> str | None:
            value = values.get(name)
            return str(value) if value not in (None, "") else None

        return cls(
            source_id=source_id,
            windows=optional("windows"),
            macos=optional("macos"),
            linux=optional("linux"),
        )


class UnknownSourceError(KeyError):
    """No mapping exists for a portable source id on the selected platform."""


class UnsafeRelativePathError(ValueError):
    """A server path attempted to escape its configured source root."""


class SourcePathResolver:
    def __init__(self, mappings: Iterable[SourceMapping]):
        self._mappings = {mapping.source_id: mapping for mapping in mappings}
        if "" in self._mappings:
            raise ValueError("source_id must not be empty")

    @classmethod
    def from_dict(cls, values: Mapping[str, Mapping[str, object]]) -> SourcePathResolver:
        return cls(SourceMapping.from_dict(source_id, roots) for source_id, roots in values.items())

    def resolve(
        self,
        source: SourcePath | str,
        relative_path: str | None = None,
        *,
        platform: PlatformFamily | str | None = None,
    ) -> PurePath:
        source_id, relative = self._parts(source, relative_path)
        family = PlatformFamily(platform) if platform is not None else PlatformFamily.current()
        mapping = self._mappings.get(source_id)
        root = mapping.root_for(family) if mapping is not None else None
        if not root:
            raise UnknownSourceError(f"No {family.value} mapping configured for {source_id!r}")
        components = self._relative_components(relative)
        if family is PlatformFamily.WINDOWS:
            return PureWindowsPath(root).joinpath(*components)
        return PurePosixPath(root).joinpath(*components)

    def resolve_native(self, source: SourcePath | str, relative_path: str | None = None) -> Path:
        return Path(self.resolve(source, relative_path, platform=PlatformFamily.current()))

    @staticmethod
    def _parts(source: SourcePath | str, relative_path: str | None) -> tuple[str, str]:
        if isinstance(source, str):
            if relative_path is None:
                raise TypeError("relative_path is required when source_id is passed directly")
            return source, relative_path
        if relative_path is not None:
            raise TypeError("relative_path must be omitted when SourcePath is used")
        return str(source.source_id), str(source.relative_path)

    @staticmethod
    def _relative_components(relative_path: str) -> tuple[str, ...]:
        # Contracts use POSIX separators even when the consuming client is on Windows.
        value = relative_path.replace("\\", "/")
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or value.startswith("//"):
            raise UnsafeRelativePathError("source paths must be relative")
        components = tuple(part for part in candidate.parts if part not in ("", "."))
        if not components or any(part == ".." for part in components):
            raise UnsafeRelativePathError("source paths must stay below their source root")
        if components[0].endswith(":"):
            raise UnsafeRelativePathError("drive-qualified source paths are not portable")
        return components
