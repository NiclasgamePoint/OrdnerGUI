"""Upload verified artifacts; publish the update manifest only after all assets exist."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from release_metadata import version as package_version
from release_source_assets import source_assets, server_source_assets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/client/src"))
from papagui_client.updates.feed import API, COMPATIBILITY_FLOOR, DATA_EPOCH, IMAGE_REPOSITORY, MANIFEST_NAME, REPOSITORY, Release, UpdateError


def gh(*arguments):
    return subprocess.check_output(["gh", *arguments], text=True)


def upload_asset(release, path: Path) -> None:
    """An immutable release asset may only be reused with identical contents."""
    assets = json.loads(gh("api", f"repos/{REPOSITORY}/releases/{release['id']}/assets?per_page=100"))
    matches = [asset for asset in assets if asset["name"] == path.name]
    if matches:
        with path.open("rb") as stream:
            digest = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        if len(matches) != 1 or matches[0].get("digest") != digest:
            raise UpdateError("An existing release asset has different contents; never overwrite it")
        return
    gh("release", "upload", release["tag_name"], str(path), "--repo", REPOSITORY)


def publish(root: Path, tag: str, server_digest: str) -> None:
    current = package_version("client")
    if tag not in (current, "v" + current):
        raise UpdateError("Release tag differs from package version")
    release = json.loads(gh("api", f"repos/{REPOSITORY}/releases/tags/{tag}"))
    if release["draft"] or release["prerelease"]:
        raise UpdateError("Only stable published releases create an update feed")
    descriptors = list(root.rglob("*-x64.json")) + list(root.rglob("macos-arm64.json"))
    clients = {}
    for path in descriptors:
        descriptor = json.loads(path.read_text(encoding="utf-8"))
        if descriptor["version"] != current or descriptor["platform"] in clients:
            raise UpdateError("Duplicate or mismatched platform descriptor")
        artifact = path.parent / descriptor["name"]
        with artifact.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != descriptor["sha256"] or artifact.stat().st_size != descriptor["size"]:
            raise UpdateError("Downloaded build artifact differs from its descriptor")
        clients[descriptor["platform"]] = {key: descriptor[key] for key in ("name", "size", "sha256")}
    manifest = {"schema": 1, "version": current, "data_epoch": DATA_EPOCH, "api_versions": ["2", "1"], "compatibility_floor": COMPATIBILITY_FLOOR, "clients": clients, "server_image": IMAGE_REPOSITORY + "@" + server_digest}
    # Validate before uploading anything, using the final expected asset metadata.
    expected = [{"name": a["name"], "size": a["size"], "digest": "sha256:" + a["sha256"], "state": "uploaded", "url": API + f"/releases/assets/{i}"} for i, a in enumerate(clients.values())]
    Release.parse(manifest, {**release, "assets": expected})
    files = [p for p in root.rglob("*") if p.is_file() and not p.name.startswith("papagui-server-sources-") and p.name.startswith(("papagui-", "papagui_")) and (p.name.endswith((".whl", ".tar.gz", ".deb", ".pkg", "-setup-unsigned.exe", ".sha256")))]
    files.extend(source_assets(root) + server_source_assets(root))
    names = [p.name for p in files]
    if len(set(names)) != len(names):
        raise UpdateError("Duplicate release filenames")

    for file in sorted(files):
        upload_asset(release, file)
    final_release = json.loads(gh("api", f"repos/{REPOSITORY}/releases/{release['id']}"))
    Release.parse(manifest, final_release)
    final = root / MANIFEST_NAME
    final.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    upload_asset(release, final)  # The activation signal is deliberately the last upload.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--server-digest", required=True)
    args = parser.parse_args()
    publish(args.artifacts, args.tag, args.server_digest)
