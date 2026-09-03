from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from papagui_server.api import create_app
from papagui_server.composition import RuntimeConfiguration, build_container


def _container(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    return build_container(
        RuntimeConfiguration(
            source_path=source,
            data_path=tmp_path / "data",
            config_path=tmp_path / "config",
            client_token="client-token-123",
        )
    )


def _run(coroutine):
    return asyncio.run(coroutine)


def test_health_system_info_and_client_authentication(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(_container(tmp_path), manage_lifecycle=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["status"] == "degraded"
            assert (await client.get("/v2/system/info")).status_code == 401
            response = await client.get(
                "/v2/system/info",
                headers={"Authorization": "Bearer client-token-123"},
            )
            assert response.status_code == 200
            assert response.json()["server_version"] == "0.4.2"
            assert "component_generations" in response.json()["capabilities"]["features"]

    _run(scenario())


def test_client_token_protects_settings_and_actions_without_password(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(_container(tmp_path), manage_lifecycle=False)
        client_headers = {"Authorization": "Bearer client-token-123"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/v2/admin/settings")).status_code == 401
            assert (
                await client.post(
                    "/v2/admin/session", headers=client_headers, json={"password": "old"}
                )
            ).status_code == 404
            updated = await client.put(
                "/v2/admin/settings",
                headers=client_headers,
                json={"settings": {"interval_seconds": 900}},
            )
            assert updated.status_code == 200
            assert updated.json()["settings"]["interval_seconds"] == 900
            invalid = await client.put(
                "/v2/admin/settings",
                headers=client_headers,
                json={"settings": {"interval_seconds": 899}},
            )
            assert invalid.status_code == 400
            queued = await client.post(
                "/v2/admin/index-runs",
                headers=client_headers,
                json={"full_rebuild": True},
            )
            assert queued.status_code == 202
            assert queued.json()["accepted"] is True
            assert (
                await client.get("/v2/admin/settings", headers=client_headers)
            ).status_code == 200

    _run(scenario())


def test_customer_conflict_and_idempotent_replay(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(_container(tmp_path), manage_lifecycle=False)
        headers = {"Authorization": "Bearer client-token-123"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            create_payload = {
                "customer": {"display_name": "Alpha GmbH"},
                "idempotency_key": "create-alpha-001",
            }
            created = await client.post("/v2/customers", headers=headers, json=create_payload)
            assert created.status_code == 201
            customer = created.json()["customer"]
            replay = await client.post("/v2/customers", headers=headers, json=create_payload)
            assert replay.status_code == 201
            assert replay.headers["X-Idempotent-Replay"] == "true"
            assert replay.json()["customer"]["id"] == customer["id"]
            reused = await client.post(
                "/v2/customers",
                headers=headers,
                json={
                    "customer": {"display_name": "Andere GmbH"},
                    "idempotency_key": "create-alpha-001",
                },
            )
            assert reused.status_code == 409

            first_update = await client.put(
                f"/v2/customers/{customer['id']}",
                headers={**headers, "Idempotency-Key": "update-alpha-001", "If-Match": "1"},
                json={"customer": {**customer, "display_name": "Alpha Neu"}},
            )
            assert first_update.status_code == 200
            assert first_update.json()["customer"]["revision"] == 2
            stale = await client.put(
                f"/v2/customers/{customer['id']}",
                headers={**headers, "Idempotency-Key": "update-alpha-002", "If-Match": "1"},
                json={"customer": {**customer, "display_name": "Alpha Parallel"}},
            )
            assert stale.status_code == 409
            assert stale.json()["current"]["revision"] == 2

    _run(scenario())


def test_generation_endpoints_require_complete_generation(tmp_path: Path) -> None:
    async def scenario() -> None:
        container = _container(tmp_path)
        app = create_app(container, manage_lifecycle=False)
        headers = {"Authorization": "Bearer client-token-123"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.get("/v2/generations/current", headers=headers)
            ).status_code == 404
            source_file = container.configuration.source_path / "Leistung" / "2026" / "Kunde, Berlin" / "test.txt"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("Hallo Index", encoding="utf-8")
            assert container.coordinator.run_once(full_rebuild=True) == 0
            current = await client.get("/v2/generations/current", headers=headers)
            assert current.status_code == 200
            component = current.json()["components"]["index"]
            archive = await client.get("/" + component["archive"], headers=headers)
            assert archive.status_code == 200
            assert len(archive.content) == component["size"]

    _run(scenario())
