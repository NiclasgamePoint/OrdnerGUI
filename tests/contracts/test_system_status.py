from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from papagui_contracts import (
    Capabilities,
    Capability,
    ContractValidationError,
    IndexProgress,
    IndexRunState,
    IndexStatus,
    ServerState,
    ServerStatus,
    SourcePath,
    SystemInfo,
)


class SystemContractTests(unittest.TestCase):
    def test_system_info_roundtrip_preserves_known_and_future_capabilities(self):
        original = SystemInfo(
            server_version="0.4.2",
            capabilities=Capabilities(
                api_versions=("2", "1"),
                generation_schema_versions=(2, 1),
                features=(Capability.CUSTOMER_REVISIONS.value, "future_feature"),
            ),
        )

        restored = SystemInfo.from_json(original.to_json())

        self.assertEqual(restored, original)
        self.assertTrue(restored.capabilities.supports(Capability.CUSTOMER_REVISIONS))
        self.assertTrue(restored.capabilities.supports("future_feature"))
        with self.assertRaises(FrozenInstanceError):
            restored.server_version = "changed"  # type: ignore[misc]

    def test_v1_boolean_capabilities_and_version_names_are_accepted(self):
        parsed = SystemInfo.from_dict(
            {
                "version": "0.4.1",
                "capabilities": {
                    "customer_revisions": True,
                    "component_generations": False,
                },
            }
        )

        self.assertEqual(parsed.server_version, "0.4.1")
        self.assertEqual(parsed.api_version, "1")
        self.assertEqual(parsed.capabilities.api_versions, ("1",))
        self.assertEqual(parsed.capabilities.generation_schema_versions, (1,))
        self.assertEqual(parsed.capabilities.features, ("customer_revisions",))

    def test_capability_lists_must_not_be_empty(self):
        with self.assertRaises(ContractValidationError):
            Capabilities(api_versions=())


class StatusContractTests(unittest.TestCase):
    def test_v2_status_roundtrip_includes_portable_progress_path(self):
        original = ServerStatus(
            state=ServerState.ONLINE,
            server_version="0.4.2",
            uptime_seconds=42,
            observed_at="2026-09-02T10:00:00+00:00",
            active_index_generation="index-2",
            active_customer_generation="customers-7",
            index=IndexStatus(
                state=IndexRunState.RUNNING,
                run_id="run-1",
                progress=IndexProgress(
                    processed_items=5,
                    total_items=10,
                    phase="catalog",
                    current_source=SourcePath("projects", "2026/A/file.pdf"),
                ),
            ),
        )

        restored = ServerStatus.from_json(original.to_json())

        self.assertEqual(restored, original)
        self.assertTrue(restored.index.is_active)
        self.assertEqual(restored.index.progress.fraction, 0.5)

    def test_v1_nested_status_is_normalized(self):
        parsed = ServerStatus.from_dict(
            {
                "server": {
                    "status": "online",
                    "version": "0.4.1",
                    "uptime_seconds": 8,
                },
                "job": {
                    "status": "running",
                    "processed_count": 12,
                    "total_count": 30,
                    "current_path": "/source/Kunde/Datei.pdf",
                },
                "generation": {"generation": "combined-1"},
            }
        )

        self.assertIs(parsed.state, ServerState.ONLINE)
        self.assertIs(parsed.index.state, IndexRunState.RUNNING)
        self.assertEqual(parsed.index.progress.processed_items, 12)
        self.assertEqual(
            parsed.index.progress.legacy_current_path,
            "/source/Kunde/Datei.pdf",
        )
        self.assertEqual(parsed.active_index_generation, "combined-1")
        self.assertEqual(parsed.active_customer_generation, "combined-1")

    def test_unknown_forward_status_is_safe_and_negative_counts_fail(self):
        self.assertIs(ServerState.parse("future"), ServerState.UNKNOWN)
        self.assertIs(IndexRunState.parse("future"), IndexRunState.UNKNOWN)
        with self.assertRaises(ContractValidationError):
            IndexProgress(processed_items=-1)


if __name__ == "__main__":
    unittest.main()
