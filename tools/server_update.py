"""Update one configured Compose service with offline backups and rollback.

Run on the Docker host using Python 3.11+, or from the documented updater
container with the same absolute bind paths. Never mount this into the server.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/client/src"))

from papagui_client.updates.feed import ReleaseFeed, UpdateError, version
from papagui_client.updates.storage import FileLock, atomic_json, private_directory, snapshot, preserve_ownership


@dataclass(frozen=True)
class Configuration:
    compose_files: tuple[Path, ...]
    project_directory: Path
    project_name: str
    service: str
    data: Path
    config: Path
    state: Path
    env_file: Path | None = None

    @classmethod
    def load(cls, path: Path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        def absolute(value):
            candidate = Path(value)
            if not candidate.is_absolute() or candidate.is_symlink():
                raise UpdateError("Updater paths must be absolute and must not be symlinks")
            result = candidate.resolve()
            if result.parent == result:
                raise UpdateError("A filesystem root cannot be an update data directory")
            return result
        result = cls(tuple(absolute(p) for p in raw["compose_files"]), absolute(raw["project_directory"]), raw["project_name"], raw.get("service", "papagui-server"), absolute(raw["data"]), absolute(raw["config"]), absolute(raw["state"]), absolute(raw["env_file"]) if raw.get("env_file") else None)
        for name in (result.project_name, result.service):
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
                raise UpdateError("Invalid Compose project or service name")
        paths = (result.data, result.config, result.state)
        if any(a.is_relative_to(b) for a in paths for b in paths if a != b) or len(set(paths)) != 3:
            raise UpdateError("Data, config and updater state must be separate directories")
        if not result.compose_files or not all(p.is_file() for p in result.compose_files):
            raise UpdateError("Compose configuration is missing")
        return result


class ServerUpdater:
    def __init__(self, config: Configuration, *, feed=None, run=None):
        self.config = config
        self.feed = feed or ReleaseFeed()
        self.run = run or self._run
        self.journal = config.state / "transaction.json"
        self.override = config.state / "image.compose.json"

    @staticmethod
    def _run(command, *, timeout=120):
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.SubprocessError:
            # Docker output may contain deployment environment values.
            raise UpdateError("Docker operation failed; inspect the local service") from None
        return result.stdout.strip()

    def compose(self, *arguments):
        c = self.config
        command = ["docker", "compose", "--project-name", c.project_name, "--project-directory", str(c.project_directory)]
        if c.env_file:
            command += ["--env-file", str(c.env_file)]
        for path in c.compose_files:
            command += ["-f", str(path)]
        if self.override.exists():
            command += ["-f", str(self.override)]
        return self.run(command + list(arguments))

    def container(self, *, required=True):
        identifier = self.compose("ps", "-a", "-q", self.config.service).splitlines()
        if not identifier and not required:
            return None
        if len(identifier) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", identifier[0]):
            raise UpdateError("Expected exactly one existing server container")
        info = json.loads(self.run(["docker", "inspect", identifier[0]]))[0]
        mounts = {m["Destination"]: m for m in info["Mounts"]}
        for destination, directory in (("/data", self.config.data), ("/config", self.config.config)):
            mount = mounts.get(destination, {})
            if mount.get("Type") != "bind" or Path(mount.get("Source", "")).resolve() != directory:
                raise UpdateError("Configured directories do not match the running container")
        if mounts.get("/source", {}).get("RW") is not False:
            raise UpdateError("The document source must remain read-only")
        source = Path(mounts["/source"]["Source"]).resolve()
        for path in (self.config.data, self.config.config, self.config.state):
            if path.is_relative_to(source) or source.is_relative_to(path):
                raise UpdateError("Updater directories must not overlap the document source")
        return info

    def set_image(self, image):
        atomic_json(self.override, {"services": {self.config.service: {"image": image, "pull_policy": "never", "environment": {"PAPAGUI_UPDATE_HOLD_FILE": "/config/.update-hold"}}}})

    def stop(self):
        self.compose("stop", "--timeout", "60", self.config.service)
        current = self.container(required=False)
        if current and current["State"]["Running"]:
            raise UpdateError("Server did not stop; backup refused")

    def start(self, image):
        self.set_image(image)
        self.compose("up", "-d", "--no-build", "--no-deps", "--force-recreate", self.config.service)

    def health(self, expected, *, timeout=180):
        deadline = time.monotonic() + timeout
        code = (
            "import json,urllib.request,sqlite3;from pathlib import Path;"
            "d=json.load(urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=3));"
            f"assert d['server_version']=={expected!r};"
            "assert d['status'] in ('ok','degraded');"
            "paths=[p for p in Path('/data').rglob('*.db') if not p.is_symlink()];"
            "exec(\"for p in paths:\\n c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)\\n try: assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'\\n finally: c.close()\")"
        )
        while time.monotonic() < deadline:
            info = self.container()
            if info["State"]["Running"]:
                try:
                    self.run(["docker", "exec", info["Id"], "python", "-c", code], timeout=60)
                    return
                except UpdateError:
                    pass
            time.sleep(2)
        raise UpdateError("Updated server failed startup/integrity checks")

    def restore(self, transaction):
        self.stop()
        backup = self.config.state / "backups" / transaction["backup"]
        if not backup.resolve().is_relative_to((self.config.state / "backups").resolve()):
            raise UpdateError("Invalid backup path")
        for name, target in (("data", self.config.data), ("config", self.config.config)):
            source = backup / name
            if not source.is_dir():
                raise UpdateError("Required rollback snapshot is missing")
            # Restore next to the original root. Rename on the same filesystem;
            # keep the failed candidate data, including any diagnostic files.
            prepared = target.with_name(target.name + ".restore-" + uuid.uuid4().hex)
            shutil.copytree(source, prepared, symlinks=True)
            preserve_ownership(source, prepared)
            failed = target.with_name(target.name + ".failed-" + uuid.uuid4().hex)
            if target.exists():
                os.replace(target, failed)
            os.replace(prepared, target)
        transaction["phase"] = "rollback-committed"
        atomic_json(self.journal, transaction)
        self.start(transaction["previous_image"])
        self.health(transaction["previous_version"])
        atomic_json(self.config.state / "rejected.json", {"version": transaction["version"]})
        self.journal.unlink()

    def recover(self):
        if not self.journal.exists():
            return
        transaction = json.loads(self.journal.read_text(encoding="utf-8"))
        if transaction["phase"] == "committed":
            (self.config.config / ".update-hold").unlink(missing_ok=True)
            self.journal.unlink()
        elif transaction["phase"] == "rollback-committed":
            current = self.container(required=False)
            if current is None or not current["State"]["Running"]:
                self.start(transaction["previous_image"])
            self.health(transaction["previous_version"])
            atomic_json(self.config.state / "rejected.json", {"version": transaction["version"]})
            self.journal.unlink()
        elif transaction["phase"] == "backed-up":
            self.restore(transaction)
        else:
            self.start(transaction["previous_image"])
            self.journal.unlink()

    def tick(self):
        private_directory(self.config.state)
        with FileLock(self.config.state / "update.lock"):
            self.recover()
            release = self.feed.latest()
            if release is None:
                return False
            rejected = self.config.state / "rejected.json"
            if rejected.exists() and json.loads(rejected.read_text())["version"] == release.version:
                return False
            current = self.container()
            current_version = current["Config"].get("Labels", {}).get("org.opencontainers.image.version", "")
            if version(release.version) <= version(current_version):
                return False
            if not current["State"]["Running"]:
                return False  # Respect a deliberately stopped installation.
            self.run(["docker", "pull", release.server_image], timeout=900)
            backup_id = release.version + "-" + uuid.uuid4().hex
            transaction = {"version": release.version, "previous_image": current["Image"], "previous_version": current_version, "backup": backup_id, "phase": "prepared"}
            atomic_json(self.journal, transaction)
            try:
                self.stop()
                backup = self.config.state / "backups" / backup_id
                backup.mkdir(parents=True)
                snapshot(self.config.data, backup / "data")
                snapshot(self.config.config, backup / "config")
                transaction["phase"] = "backed-up"
                atomic_json(self.journal, transaction)
                (self.config.config / ".update-hold").write_text("update validation in progress", encoding="utf-8")
                self.start(release.server_image)
                self.health(release.version)
                # Commit before exposing writes. A power loss before this point
                # restores the old offline snapshot; after it, user writes stay.
                transaction["phase"] = "committed"
                atomic_json(self.journal, transaction)
                (self.config.config / ".update-hold").unlink()
                self.journal.unlink()
                atomic_json(self.config.state / "status.json", {"version": release.version, "status": "updated", "backup": backup_id})
                return True
            except Exception:
                if transaction["phase"] == "backed-up":
                    self.restore(transaction)
                elif transaction["phase"] == "prepared":
                    self.start(current["Image"])
                    self.journal.unlink(missing_ok=True)
                raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=3600)
    arguments = parser.parse_args(argv)
    if arguments.interval < 300:
        parser.error("Update interval must be at least 300 seconds")
    updater = ServerUpdater(Configuration.load(arguments.config))
    while True:
        try:
            changed = updater.tick()
            print("Server updated." if changed else "No update required.", flush=True)
        except Exception:
            print("Update failed or deferred. Inspect the local updater state; existing backups are retained.", file=sys.stderr, flush=True)
            if not arguments.watch:
                return 1
        if not arguments.watch:
            return 0
        time.sleep(arguments.interval)


if __name__ == "__main__":
    raise SystemExit(main())
