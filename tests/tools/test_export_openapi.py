from __future__ import annotations

import json

from tools import export_openapi


def test_generated_openapi_has_versioned_products_and_health() -> None:
    payload = export_openapi.generate()

    assert payload["info"]["version"] == "0.4.3"
    assert "/health" in payload["paths"]
    assert "/v2/generations/current" in payload["paths"]
    assert "/v2/admin/session" not in payload["paths"]
    assert "/v2/admin/settings" in payload["paths"]


def test_check_reports_drift_and_accepts_exact_snapshot(tmp_path) -> None:
    output = tmp_path / "openapi.json"
    output.write_text("{}\n", encoding="utf-8")
    assert export_openapi.main(["--check", "--output", str(output)]) == 1

    output.write_text(export_openapi.serialized(export_openapi.generate()), encoding="utf-8")
    assert export_openapi.main(["--check", "--output", str(output)]) == 0
    json.loads(output.read_text(encoding="utf-8"))
