"""Require the pinned upstream source archives before publishing native binaries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from release_metadata import ROOT


SOURCE_MANIFEST = ROOT / "packaging/upstream-sources.json"


def source_assets(root: Path) -> list[Path]:
    from papagui_client.updates.feed import UpdateError

    result = []
    for entry in json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))["archives"]:
        matches = [path for path in root.rglob(entry["name"]) if path.is_file()]
        if len(matches) != 1:
            raise UpdateError("Exactly one pinned upstream source archive is required")
        with matches[0].open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"]:
            raise UpdateError("Upstream source archive checksum mismatch")
        result.extend(matches)
    return result


def server_source_assets(root: Path) -> list[Path]:
    from papagui_client.updates.feed import UpdateError

    result = []
    for architecture in ("amd64", "arm64"):
        matches = list(root.rglob(f"papagui-server-sources-{architecture}.tar.gz"))
        if len(matches) != 1:
            raise UpdateError("Missing or duplicate server source archive")
        archive = matches[0]
        checksum = archive.with_name(archive.name + ".sha256")
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if not checksum.is_file() or checksum.read_text(encoding="ascii").strip() != f"{digest}  {archive.name}":
            raise UpdateError("Server source archive checksum mismatch")
        result.extend((archive, checksum))
    return result
