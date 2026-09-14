"""Exercise real Docker update/rollback using disposable documents and volumes.

Requires a Linux Docker host and a locally built candidate image. Never uses an
existing Compose project. --workspace must be visible at the same absolute path
to the updater and Docker daemon (also when running in a helper container).
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import uuid

from server_update import Configuration, ServerUpdater
from papagui_client.updates.feed import UpdateError


def check(image: str, previous_ref: str, workspace: Path | None, previous_archive: Path | None = None):
    root = Path(__file__).resolve().parents[1]
    name = "papagui-update-test-" + uuid.uuid4().hex[:12]
    previous_image = name + ":previous"
    with TemporaryDirectory(prefix=name, dir=workspace) as temporary:
        temporary = Path(temporary)
        baseline = temporary / "baseline"
        baseline.mkdir()
        archived = previous_archive.read_bytes() if previous_archive else subprocess.check_output(["git", "archive", previous_ref, "packages/contracts", "packages/server", "deploy/server/Dockerfile", ".dockerignore"], cwd=root)
        with tarfile.open(fileobj=io.BytesIO(archived)) as source:
            source.extractall(baseline, filter="data")
        subprocess.run(["docker", "build", "-q", "-f", str(baseline / "deploy/server/Dockerfile"), "-t", previous_image, str(baseline)], check=True)
        candidate_version = subprocess.check_output(["docker", "inspect", "--format", '{{index .Config.Labels "org.opencontainers.image.version"}}', image], text=True).strip()
        candidate = subprocess.check_output(["docker", "inspect", "--format", "{{.Id}}", image], text=True).strip()
        for broken in (True, False):
            case = temporary / ("rollback" if broken else "success")
            case.mkdir()
            source, data, config, state = (case / p for p in ("source", "data", "config", "updates"))
            for directory in (source, data, config):
                directory.mkdir()
            (source / "customer.txt").write_text("synthetic document")
            (data / "user-notes.txt").write_text("preserve customer data")
            (config / "api-token").write_text("synthetic-test-token")
            compose = case / "compose.json"
            compose.write_text(json.dumps({"services": {"papagui-server": {
                "image": previous_image, "user": f"{os.getuid()}:{os.getgid()}", "command": ["serve", "--no-run-on-start"],
                "environment": {"PAPAGUI_API_TOKEN_FILE": "/config/api-token"},
                "volumes": [f"{source}:/source:ro", f"{data}:/data", f"{config}:/config"],
            }}}))
            deployment = Configuration((compose,), case, name, "papagui-server", data, config, state)
            release = SimpleNamespace(version=candidate_version, server_image=candidate)
            def local_run(command, **kwargs):
                if command[:2] == ["docker", "pull"]:
                    return ""  # Candidate is built locally; all other Docker calls are real.
                return ServerUpdater._run(command, **kwargs)
            updater = ServerUpdater(deployment, feed=SimpleNamespace(latest=lambda: release), run=local_run)
            try:
                updater.compose("up", "-d", "--no-build")
                previous_version = updater.container()["Config"]["Labels"]["org.opencontainers.image.version"]
                updater.health(previous_version)
                original_health = updater.health
                def health(expected):
                    original_health(expected)
                    if expected == candidate_version:
                        probe = "import urllib.request,urllib.error;\ntry: urllib.request.urlopen('http://127.0.0.1:8765/v2/generations/current')\nexcept urllib.error.HTTPError as e: assert e.code == 503\nelse: raise AssertionError('API was not held')"
                        local_run(["docker", "exec", updater.container()["Id"], "python", "-c", probe])
                        if broken:
                            (data / "user-notes.txt").write_text("synthetic failed migration")
                            raise UpdateError("deliberately rejected candidate")
                updater.health = health
                if broken:
                    try:
                        updater.tick()
                    except UpdateError:
                        pass
                    else:
                        raise AssertionError("Expected rollback")
                    assert updater.container()["Config"]["Labels"]["org.opencontainers.image.version"] == previous_version
                else:
                    assert updater.tick()
                    assert updater.container()["Image"] == candidate
                assert (data / "user-notes.txt").read_text() == "preserve customer data"
                assert (config / "api-token").read_text() == "synthetic-test-token"
                assert (source / "customer.txt").read_text() == "synthetic document"
                assert not (config / ".update-hold").exists()
                assert not updater.journal.exists()
                print("Rollback passed." if broken else "Update passed.", flush=True)
            finally:
                updater.compose("down", "--remove-orphans")
        subprocess.run(["docker", "image", "rm", previous_image], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--previous-ref", default="0.4.3")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--previous-archive", type=Path)
    args = parser.parse_args()
    check(args.image, args.previous_ref, args.workspace, args.previous_archive)
