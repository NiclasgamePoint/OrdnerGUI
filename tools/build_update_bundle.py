"""Archive tested native products for atomic per-user application updates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile
from tempfile import TemporaryDirectory

from collect_licenses import collect
from release_metadata import check, version

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/client/src"))
from papagui_client.updates.runtime import platform_key


def build(dist: Path, output: Path) -> Path:
    check()
    target = platform_key()
    output.mkdir(parents=True, exist_ok=True)
    name = f"papagui-client-{version('client')}-{target}-update.tar.gz"
    artifact = output / name
    products = ("PapaGUI Client.app", "PapaGUI Tray.app") if sys.platform == "darwin" else tuple("papagui-" + role + (".exe" if sys.platform == "win32" else "") for role in ("client", "tray"))
    with TemporaryDirectory(prefix="papagui-update-licenses-") as directory:
        notices = Path(directory) / "licenses"
        collect("client", notices)
        with tarfile.open(artifact, "x:gz", dereference=False) as archive:
            for product in products:
                archive.add(dist / product, arcname=product)
            archive.add(notices, arcname="licenses")
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    descriptor = {"platform": target, "version": version("client"), "name": name, "size": artifact.stat().st_size, "sha256": digest}
    (output / f"{target}.json").write_text(json.dumps(descriptor, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--output", type=Path, default=Path("dist/updates"))
    args = parser.parse_args()
    print(build(args.dist, args.output))
