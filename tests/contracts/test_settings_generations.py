from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from papagui_contracts import (
    MAX_INDEX_INTERVAL_SECONDS,
    MIN_INDEX_INTERVAL_SECONDS,
    ContractValidationError,
    GenerationComponentKind,
    GenerationComponentManifest,
    GenerationManifest,
    IndexSettings,
    ResourceProfile,
    SourcePath,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


class IndexSettingsContractTests(unittest.TestCase):
    def test_interval_boundaries_are_inclusive_and_roundtrip(self):
        for interval in (MIN_INDEX_INTERVAL_SECONDS, MAX_INDEX_INTERVAL_SECONDS):
            settings = IndexSettings(
                interval_seconds=interval,
                resource_profile=ResourceProfile.FAST,
            )
            self.assertEqual(IndexSettings.from_json(settings.to_json()), settings)

    def test_interval_outside_15_minutes_to_48_hours_is_rejected(self):
        for interval in (
            MIN_INDEX_INTERVAL_SECONDS - 1,
            MAX_INDEX_INTERVAL_SECONDS + 1,
        ):
            with self.subTest(interval=interval):
                with self.assertRaises(ContractValidationError):
                    IndexSettings(interval_seconds=interval)

    def test_v1_automatic_monitoring_name_is_accepted(self):
        settings = IndexSettings.from_dict(
            {
                "interval_seconds": 3600.0,
                "automatic_monitoring_enabled": False,
                "resource_profile": "gentle",
            }
        )

        self.assertEqual(settings.interval_seconds, 3600)
        self.assertFalse(settings.automatic_runs_enabled)
        self.assertFalse(settings.automatic_monitoring_enabled)
        self.assertIs(settings.resource_profile, ResourceProfile.GENTLE)
        self.assertEqual(settings.to_dict()["resource_profile"], "gentle")
        with self.assertRaises(FrozenInstanceError):
            settings.interval_seconds = 900  # type: ignore[misc]

    def test_invalid_profile_and_inconsistent_ocr_pages_fail(self):
        with self.assertRaises(ContractValidationError):
            IndexSettings.from_dict({"resource_profile": "turbo"})
        with self.assertRaises(ContractValidationError):
            IndexSettings(ocr_max_pages=30, ocr_extended_max_pages=20)


class GenerationContractTests(unittest.TestCase):
    @staticmethod
    def component(
        kind: GenerationComponentKind,
        generation: str,
        sha256: str,
    ) -> GenerationComponentManifest:
        return GenerationComponentManifest(
            kind=kind,
            generation=generation,
            created_at="2026-09-02T10:00:00+00:00",
            archive=f"{kind.value}/{generation}.zip",
            size=123,
            sha256=sha256,
        )

    def test_v2_manifest_keeps_index_and_customer_components_independent(self):
        original = GenerationManifest(
            created_at="2026-09-02T10:00:01+00:00",
            index=self.component(GenerationComponentKind.INDEX, "index-2", SHA_A),
            customers=self.component(GenerationComponentKind.CUSTOMERS, "customers-9", SHA_B),
        )

        wire = original.to_dict()
        restored = GenerationManifest.from_json(original.to_json(indent=2))

        self.assertEqual(wire["schema_version"], 2)
        self.assertEqual(
            wire["components"]["index"]["generation"],  # type: ignore[index]
            "index-2",
        )
        self.assertEqual(restored, original)
        self.assertEqual(restored.component("customers").generation, "customers-9")

    def test_v2_manifest_accepts_a_temporarily_missing_component(self):
        payload = {
            "schema_version": 2,
            "created_at": "2026-09-02T10:00:01+00:00",
            "index": None,
            "customers": self.component(
                GenerationComponentKind.CUSTOMERS, "customers-1", SHA_B
            ).to_dict(),
        }

        parsed = GenerationManifest.from_dict(payload)

        self.assertIsNone(parsed.index)
        self.assertEqual(parsed.customers.generation, "customers-1")
        self.assertEqual(GenerationManifest.from_json(parsed.to_json()), parsed)

        with self.assertRaises(ContractValidationError):
            GenerationManifest.from_dict(
                {
                    "schema_version": 2,
                    "created_at": "2026-09-02T10:00:01+00:00",
                    "components": {"index": None, "customers": None},
                }
            )

    def test_v1_combined_manifest_maps_to_both_components_and_roundtrips(self):
        v1 = {
            "schema_version": 1,
            "generation": "combined-7",
            "created_at": "2026-09-02T10:00:00+00:00",
            "archive": "combined-7.zip",
            "size": 456,
            "sha256": SHA_A,
        }

        parsed = GenerationManifest.from_dict(v1)

        self.assertTrue(parsed.legacy_combined)
        self.assertEqual(parsed.index.archive, parsed.customers.archive)
        self.assertEqual(parsed.index.generation, "combined-7")
        self.assertEqual(GenerationManifest.from_json(parsed.to_json()), parsed)

    def test_source_path_accepts_only_normalized_portable_relative_paths(self):
        source = SourcePath.from_dict({"source_id": "nas-projects", "path": "2026/Kunde/Plan.pdf"})
        self.assertEqual(source.relative_path, "2026/Kunde/Plan.pdf")
        self.assertEqual(SourcePath.from_json(source.to_json()), source)

        invalid = (
            ("bad source", "folder/file.pdf"),
            ("source", "/absolute/file.pdf"),
            ("source", "../escape.pdf"),
            ("source", "folder\\file.pdf"),
            ("source", "C:/windows.pdf"),
            ("source", "folder//file.pdf"),
        )
        for source_id, path in invalid:
            with self.subTest(source_id=source_id, path=path):
                with self.assertRaises(ContractValidationError):
                    SourcePath(source_id, path)

    def test_component_integrity_metadata_is_validated(self):
        with self.assertRaises(ContractValidationError):
            self.component(GenerationComponentKind.INDEX, "index-1", "bad")
        with self.assertRaises(ContractValidationError):
            GenerationComponentManifest(
                kind=GenerationComponentKind.INDEX,
                generation="index-1",
                created_at="now",
                archive="../index.zip",
                size=1,
                sha256=SHA_A,
            )


if __name__ == "__main__":
    unittest.main()
