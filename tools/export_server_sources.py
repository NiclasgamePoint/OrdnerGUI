"""Run inside a disposable server-image container to export its Debian sources.

Usage: python export_server_sources.py /output amd64
Never run this in a production server: it temporarily enables deb-src indexes.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request


def snapshot_sources(package: str, version: str, output: Path) -> None:
    base = "https://snapshot.debian.org"
    url = f"{base}/mr/package/{urllib.parse.quote(package, safe='')}/{urllib.parse.quote(version, safe='')}/srcfiles"
    with urllib.request.urlopen(url, timeout=120) as response:
        entries = json.load(response)["result"]
    if not entries:
        raise RuntimeError("No exact source files in Debian Snapshot")
    for entry in entries:
        digest = entry["hash"]
        if len(digest) != 40 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Invalid Debian Snapshot file identifier")
        with urllib.request.urlopen(f"{base}/mr/file/{digest}/info", timeout=120) as response:
            name = json.load(response)["result"][0]["name"]
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("Unsafe Debian source filename")
        target = output / name
        with urllib.request.urlopen(f"{base}/file/{digest}", timeout=120) as response, target.open("wb") as stream:
            shutil.copyfileobj(response, stream, 1024 * 1024)
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha1").hexdigest() != digest:
                raise ValueError("Debian Snapshot source checksum mismatch")


def export(output: Path, architecture: str) -> None:
    if architecture not in {"amd64", "arm64"}:
        raise ValueError("Unsupported release architecture")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="papagui-sources-") as temporary:
        stage = Path(temporary)
        sources = stage / "debian-sources"
        sources.mkdir()
        licenses = stage / "copyright"
        licenses.mkdir()
        rows = subprocess.check_output([
            "dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n"
        ], text=True).splitlines()
        packages = []
        required_sources = set()
        for row in rows:
            package, binary_version, source, source_version = row.split("\t")
            source = source or package.split(":")[0]
            source_version = source_version or binary_version
            required_sources.add((source, source_version))
            original = Path("/usr/share/doc") / package.split(":")[0] / "copyright"
            if original.is_file():
                shutil.copy2(original, licenses / (package.replace(":", "-") + ".txt"))
            packages.append({"package": package, "version": binary_version,
                             "source": source, "source_version": source_version})
        debian_sources = Path("/etc/apt/sources.list.d/debian.sources")
        config = debian_sources.read_text()
        if "Types: deb\n" not in config:
            raise RuntimeError("Expected Debian deb822 source configuration")
        debian_sources.write_text(config.replace("Types: deb\n", "Types: deb deb-src\n"))
        subprocess.run(["apt-get", "update"], check=True, stdout=subprocess.DEVNULL)
        for source, source_version in sorted(required_sources):
            print(f"Archiving Debian source {source}={source_version}", flush=True)
            result = subprocess.run(["apt-get", "source", "--download-only", "--only-source",
                                     f"{source}={source_version}"], cwd=sources,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if result.returncode:
                print("Using Debian Snapshot for the exact installed source version", flush=True)
                snapshot_sources(source, source_version, sources)
        (stage / "debian-packages.json").write_text(json.dumps(packages, indent=2) + "\n")
        python_metadata = subprocess.check_output([sys.executable, "-m", "pip", "inspect", "--local"])
        # pip inspect may include absolute install paths; retain only upstream metadata.
        installed = json.loads(python_metadata)["installed"]
        (stage / "python-packages.json").write_text(json.dumps([entry["metadata"] for entry in installed], indent=2) + "\n")
        import importlib.metadata

        for distribution in importlib.metadata.distributions():
            name = distribution.metadata["Name"]
            destination = stage / "python-notices" / name
            destination.mkdir(parents=True, exist_ok=True)
            for file in distribution.files or []:
                if any(word in file.name.casefold() for word in ("license", "licence", "copying", "copyright", "notice")):
                    path = Path(distribution.locate_file(file))
                    if path.is_file():
                        filename = hashlib.sha256(str(file).encode()).hexdigest()[:16] + "-" + file.name
                        shutil.copy2(path, destination / filename)
        artifact = output / f"papagui-server-sources-{architecture}.tar.gz"
        with tarfile.open(artifact, "x:gz", compresslevel=1) as archive:
            archive.add(stage, arcname="server-sources")
        with artifact.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        artifact.with_name(artifact.name + ".sha256").write_text(f"{digest}  {artifact.name}\n", encoding="ascii")
        print(f"Saved {artifact.name}: {len(packages)} installed packages with exact Debian source versions.")


if __name__ == "__main__":
    export(Path(sys.argv[1]), sys.argv[2])
