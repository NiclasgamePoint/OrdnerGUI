from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import patch

from app.services.index_distribution import (
    IndexGenerationClient,
    IndexGenerationPublisher,
    IndexSyncError,
)
from tests.base.test_case import PapaGuiTestCase


def _database(path: Path, statement: str = "CREATE TABLE values_(value TEXT)") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(statement)
    connection.commit()
    connection.close()


class IndexDistributionTests(PapaGuiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_data = self.temp_path / "server"
        _database(self.server_data / "customers.db")
        _database(self.server_data / "index" / "catalog" / "active.db")
        _database(self.server_data / "index" / "content" / "state.db")

    def test_publisher_creates_checksums_and_retains_three_backups(self):
        publisher = IndexGenerationPublisher(self.server_data)
        published = []
        for index in range(5):
            with patch(
                "app.services.index_distribution.datetime"
            ) as clock:
                clock.now.return_value.strftime.return_value = f"generation-{index}"
                clock.now.return_value.isoformat.return_value = f"time-{index}"
                published.append(publisher.publish())
        current = json.loads(publisher.current_manifest.read_text(encoding="utf-8"))
        self.assertEqual(current["generation"], "generation-4")
        self.assertEqual(len(list(publisher.generations.glob("*.zip"))), 4)
        self.assertFalse(published[0].archive.exists())

    def test_client_verifies_activates_and_keeps_offline_generation(self):
        published = IndexGenerationPublisher(self.server_data).publish()
        remote = json.loads(published.manifest.read_text(encoding="utf-8"))
        client = IndexGenerationClient("http://server", self.temp_path / "client")
        client._get_json = lambda _path: remote
        client._download = lambda _path, destination: shutil.copy2(
            published.archive, destination
        )
        self.assertTrue(client.sync())
        self.assertTrue(client.has_local_generation())
        self.assertFalse(client.sync())
        self.assertTrue((client.current_link / "customers.db").is_file())

        remote["sha256"] = "0" * 64
        remote["generation"] = "new"
        with self.assertRaises(IndexSyncError):
            client.sync()
        self.assertEqual(client.current_link.resolve().name, published.generation)

    def test_bootstrap_seeds_existing_local_data_once(self):
        client = IndexGenerationClient("http://server", self.temp_path / "client")
        self.assertTrue(client.bootstrap_from(self.server_data))
        self.assertFalse(client.bootstrap_from(self.server_data))
        self.assertEqual(client.current_link.resolve().name, "bootstrap-local")
