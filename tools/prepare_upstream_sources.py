"""Download hash-pinned Qt/PySide sources and preserve their third-party notices.

Archive files are kept intact for distribution beside the installers. Only
regular notice files are extracted; no archive paths, links or code are executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import urllib.request

from release_metadata import ROOT


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_notices(archive: Path, output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    notices = []
    with tarfile.open(archive, "r|xz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            name = path.name.casefold()
            wanted = any(word in name for word in ("license", "licence", "copying", "copyright", "notice"))
            wanted = wanted or name in {"qt_attribution.json", "readme.chromium"}
            if not wanted or not member.isfile() or member.size > 8 * 1024 * 1024:
                continue
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe upstream archive path")
            stream = source.extractfile(member)
            assert stream is not None
            data = stream.read()
            # Skip binaries and build scripts whose names happen to match.
            if b"\0" in data or path.suffix.lower() in {".py", ".cpp", ".h", ".png", ".pdf", ".cmake"}:
                continue
            filename = hashlib.sha256(member.name.encode()).hexdigest()[:20] + ".txt"
            (output / filename).write_bytes(data)
            notices.append({"source": member.name, "file": filename,
                            "sha256": hashlib.sha256(data).hexdigest()})
    if not notices:
        raise ValueError("No notices found in upstream source archive")
    return notices


def prepare(sources: Path, notices: Path, manifest: Path) -> None:
    config = json.loads(manifest.read_text(encoding="utf-8"))
    sources.mkdir(parents=True, exist_ok=True)
    notices.mkdir(parents=True, exist_ok=True)
    inventory = []
    for entry in config["archives"]:
        name = entry["name"]
        if Path(name).name != name or not name.endswith(".tar.xz"):
            raise ValueError("Invalid source archive filename")
        target = sources / name
        if not target.exists():
            print(f"Downloading {name}", flush=True)
            temporary = target.with_suffix(".part")
            try:
                with urllib.request.urlopen(entry["url"], timeout=120) as response, temporary.open("wb") as stream:
                    shutil.copyfileobj(response, stream, 1024 * 1024)
                if digest(temporary) != entry["sha256"]:
                    raise ValueError("Upstream source checksum mismatch")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        if digest(target) != entry["sha256"]:
            raise ValueError("Cached upstream source checksum mismatch")
        print(f"Collecting notices from {name}", flush=True)
        entries = extract_notices(target, notices / name.removesuffix(".tar.xz"))
        inventory.append({**entry, "notices": entries})
    (notices / "upstream-source-inventory.json").write_text(
        json.dumps({"schema": 1, "qt_version": config["qt_version"], "archives": inventory}, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(manifest, sources / "upstream-sources.json")
    print(f"Verified {len(inventory)} upstream archives and preserved their notices.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "build/upstream-sources")
    parser.add_argument("--notices", type=Path, default=ROOT / "build/upstream-notices")
    parser.add_argument("--manifest", type=Path, default=ROOT / "packaging/upstream-sources.json")
    args = parser.parse_args()
    prepare(args.sources, args.notices, args.manifest)
