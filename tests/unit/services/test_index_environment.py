from __future__ import annotations

import builtins
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.index_capabilities import IndexCapabilities, IndexCapabilityDetector
from app.services.index_resource_policy import IndexResourcePolicy, ResourceSnapshot
from tests.base.test_case import PapaGuiTestCase


class IndexEnvironmentTests(PapaGuiTestCase):
    def test_capability_value_reports_combined_ocr_availability(self):
        self.assertTrue(IndexCapabilities("ocr", "poppler").ocr_available)
        self.assertFalse(IndexCapabilities("ocr", "").ocr_available)

    def test_detector_handles_missing_tools_and_tesseract_failures(self):
        detector = IndexCapabilityDetector()
        with (
            patch.object(detector, "_find_tesseract", return_value=""),
            patch("app.services.index_capabilities.IndexToolResolver") as resolver,
        ):
            resolver.return_value.resolve.return_value = None
            self.assertEqual(detector.detect(), IndexCapabilities())
        for result in (
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "languages\n eng \n\ndeu\n", ""),
        ):
            with patch("app.services.index_capabilities.subprocess.run", return_value=result):
                languages = detector._tesseract_languages(Path("tesseract"))
            self.assertEqual(languages, () if result.returncode else ("deu", "eng"))
        with patch("app.services.index_capabilities.subprocess.run", side_effect=OSError):
            self.assertEqual(detector._tesseract_languages(Path("tesseract")), ())
        with (
            patch.object(detector, "_find_tesseract", return_value="/ocr"),
            patch.object(detector, "_tesseract_languages", return_value=("deu",)),
            patch("app.services.index_capabilities.IndexToolResolver") as resolver,
        ):
            resolver.return_value.resolve.side_effect = [Path("/poppler"), Path("/pdftotext")]
            capabilities = detector.detect()
        self.assertEqual(capabilities.ocr_languages, ("deu",))

    def test_tesseract_lookup_covers_path_and_windows_installations(self):
        with patch("app.services.index_capabilities.shutil.which", return_value="/bin/tesseract"):
            self.assertEqual(IndexCapabilityDetector._find_tesseract(), "/bin/tesseract")
        with (
            patch("app.services.index_capabilities.shutil.which", return_value=None),
            patch("app.services.index_capabilities.sys.platform", "linux"),
        ):
            self.assertEqual(IndexCapabilityDetector._find_tesseract(), "")
        with (
            patch("app.services.index_capabilities.shutil.which", return_value=None),
            patch("app.services.index_capabilities.sys.platform", "win32"),
            patch.dict("app.services.index_capabilities.os.environ", {"PROGRAMFILES": "C:/Programs"}, clear=True),
            patch.object(Path, "exists", return_value=True),
        ):
            self.assertTrue(IndexCapabilityDetector._find_tesseract().endswith("tesseract.exe"))
        with (
            patch("app.services.index_capabilities.shutil.which", return_value=None),
            patch("app.services.index_capabilities.sys.platform", "win32"),
            patch.dict("app.services.index_capabilities.os.environ", {}, clear=True),
        ):
            self.assertEqual(IndexCapabilityDetector._find_tesseract(), "")
        with (
            patch("app.services.index_capabilities.shutil.which", return_value=None),
            patch("app.services.index_capabilities.sys.platform", "win32"),
            patch.dict(
                "app.services.index_capabilities.os.environ",
                {"PROGRAMFILES": "C:/One", "LOCALAPPDATA": "C:/Two"}, clear=True,
            ),
            patch.object(Path, "exists", side_effect=[False, True]),
        ):
            self.assertIn("C:/Two", IndexCapabilityDetector._find_tesseract())

    def test_resource_snapshot_and_limits_cover_missing_and_available_psutil(self):
        policy = IndexResourcePolicy()
        with (
            patch("app.services.index_resource_policy.os.cpu_count", return_value=None),
            patch("app.services.index_resource_policy.psutil", None),
        ):
            self.assertEqual(policy.snapshot(), ResourceSnapshot(1, 0, 0))
        memory = SimpleNamespace(total=8, available=4)
        psutil = Mock()
        psutil.virtual_memory.return_value = memory
        with (
            patch("app.services.index_resource_policy.os.cpu_count", return_value=4),
            patch("app.services.index_resource_policy.psutil", psutil),
        ):
            self.assertEqual(policy.snapshot(), ResourceSnapshot(4, 8, 4))
        unlimited_memory = ResourceSnapshot(8, 0, 0)
        self.assertEqual(policy.worker_limit("unknown", unlimited_memory), 2)
        with (
            patch.object(policy, "snapshot", return_value=ResourceSnapshot(4, 0, 0)),
            patch.object(policy, "worker_limit", return_value=2),
        ):
            self.assertTrue(policy.permits_submission(1))
            self.assertFalse(policy.permits_submission(2))

    def test_resource_policy_optional_psutil_import_fallback(self):
        original_import = builtins.__import__

        def import_without_psutil(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("missing")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_psutil):
            namespace = runpy.run_path("app/services/index_resource_policy.py")
        self.assertIsNone(namespace["psutil"])
