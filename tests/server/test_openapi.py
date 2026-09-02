from __future__ import annotations

import json
from pathlib import Path

from papagui_server.api import create_app
from papagui_server.composition import RuntimeConfiguration, build_container


def test_openapi_snapshot_is_current(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    container = build_container(
        RuntimeConfiguration(
            source_path=source,
            data_path=tmp_path / "data",
            config_path=tmp_path / "config",
            client_token="snapshot-token",
            bootstrap_admin_password="snapshot-admin-password",
        )
    )
    generated = create_app(container, manage_lifecycle=False).openapi()
    snapshot = json.loads(
        (Path(__file__).with_name("openapi-v2.json")).read_text(encoding="utf-8")
    )
    assert generated == snapshot
    schemas = generated["components"]["schemas"]
    customer_body = generated["paths"]["/v2/customers"]["post"]["requestBody"]
    assert customer_body["content"]["application/json"]["schema"]["anyOf"] == [
        {"$ref": "#/components/schemas/CustomerCreateRequest"},
        {"$ref": "#/components/schemas/MutationEnvelopeRequest"},
        {"$ref": "#/components/schemas/CustomerModel"},
    ]
    assert set(schemas["GenerationComponentsModel"]["required"]) == {
        "index",
        "customers",
    }
    assert schemas["RecognitionDecisionRequest"]["properties"]["action"]["enum"] == [
        "accept",
        "assign",
        "reject",
    ]
    assert schemas["SuggestionDecisionRequest"]["properties"]["action"]["enum"] == [
        "accept",
        "reject",
    ]
    for path in (
        "/v2/customers/{customer_id}",
        "/v2/customers/{customer_id}/suggestions/{suggestion_id}/decision",
        "/v2/admin/recognition/cases/{signature}/decision",
    ):
        method = "put" if path == "/v2/customers/{customer_id}" else "post"
        conflict = generated["paths"][path][method]["responses"]["409"]
        assert conflict["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/CustomerConflictResponse"
        }
