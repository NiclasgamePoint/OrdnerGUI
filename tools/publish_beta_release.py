"""Publish manual-install Windows/Linux beta assets, never an activation manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from publish_update_manifest import gh, upload_asset
from release_metadata import version
from release_source_assets import source_assets, server_source_assets
from papagui_client.updates.feed import IMAGE_REPOSITORY, REPOSITORY, UpdateError


def publish(root: Path, tag: str, server_digest: str) -> None:
    current = version("client")
    if tag not in (current, "v" + current):
        raise UpdateError("Release tag differs from package version")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", server_digest):
        raise UpdateError("Invalid server image digest")
    release = json.loads(gh("api", f"repos/{REPOSITORY}/releases/tags/{tag}"))
    if release.get("draft") is not False or release.get("prerelease") is not True:
        raise UpdateError("Beta publication requires a published pre-release")
    if release.get("tag_name") != tag or not release.get("published_at"):
        raise UpdateError("Release is not published under the requested tag")

    files = source_assets(root) + server_source_assets(root)
    for alternatives in (
        (f"papagui-client-{current}-windows-x64-setup-unsigned.exe",
         f"papagui-client-{current}-windows-x64-setup.exe"),
        (f"papagui-client-{current}-linux-x64.deb",),
    ):
        matches = [path for name in alternatives for path in root.rglob(name) if path.is_file()]
        if len(matches) != 1:
            raise UpdateError("Exactly one installer is required for each beta platform")
        artifact = matches[0]
        checksum = artifact.with_name(artifact.name + ".sha256")
        with artifact.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        expected = f"{digest}  {artifact.name}"
        if not checksum.is_file() or checksum.read_text(encoding="ascii").strip() != expected:
            raise UpdateError("Installer checksum is missing or does not match")
        files.extend((artifact, checksum))
        signature = checksum.with_name(checksum.name + ".asc")
        if signature.is_file():
            files.append(signature)

    # Include source distributions and wheels, but no automatic update archives.
    for component in ("client", "server", "contracts"):
        for suffix in (".tar.gz", "-py3-none-any.whl"):
            files.extend(root.rglob(f"papagui_{component}-{current}{suffix}"))
    names = [path.name for path in files]
    if len(names) != len(set(names)):
        raise UpdateError("Duplicate beta release filenames")
    for path in sorted(files):
        upload_asset(release, path)
    metadata = root / "papagui-beta.json"
    metadata.write_text(json.dumps({
        "version": current, "channel": "beta", "automatic_updates": False,
        "server_image": IMAGE_REPOSITORY + "@" + server_digest,
        "installers": [name for name in names if name.endswith((".exe", ".deb"))],
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    upload_asset(release, metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--server-digest", required=True)
    args = parser.parse_args()
    publish(args.artifacts, args.tag, args.server_digest)
