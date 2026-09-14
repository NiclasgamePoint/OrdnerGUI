"""Download, extract, probe and activate a native update with a disposable profile."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/client/src"))
from papagui_client.updates import feed, runtime, storage


def check(directory: Path):
    descriptor = json.loads((directory / (runtime.platform_key() + ".json")).read_text())
    archive = directory / descriptor["name"]
    asset = feed.Asset(descriptor["name"], descriptor["size"], descriptor["sha256"], feed.API + "/releases/assets/1")
    release = SimpleNamespace(version=descriptor["version"], clients={runtime.platform_key(): asset})
    # Replace transport only: the production downloader still checks size/hash.
    client = feed.ReleaseFeed(opener=SimpleNamespace(open=lambda *a, **k: archive.open("rb")))
    client.latest = lambda: release
    with TemporaryDirectory(prefix="papagui-native-update-") as temporary:
        base = Path(temporary)
        root, data = base / "updates", base / "profile"
        storage.private_directory(root)
        data.mkdir()
        (data / "notes.txt").write_text("preserve user edits")
        with closing(sqlite3.connect(data / "synthetic.db")) as connection, connection:
            connection.execute("CREATE TABLE notes (body TEXT)")
            connection.execute("INSERT INTO notes VALUES ('preserve outbox')")
        with patch.object(runtime, "__version__", feed.COMPATIBILITY_FLOOR):
            assert runtime.stage(root, feed.COMPATIBILITY_FLOOR, client)
            with storage.FileLock(root / "activation.lock"):
                active = runtime.activate(root, data)
        assert active["version"] == release.version
        assert (data / "notes.txt").read_text() == "preserve user edits"
        backup = root / "backups" / active["backup"] / "data"
        with closing(sqlite3.connect(backup / "synthetic.db")) as connection:
            assert connection.execute("SELECT body FROM notes").fetchone()[0] == "preserve outbox"
        assert not (root / "ready.json").exists()
    print("Native update download, activation and data backup passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    check(parser.parse_args().directory.resolve())
