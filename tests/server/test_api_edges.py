from __future__ import annotations

import asyncio
from pathlib import Path
from types import MethodType

import httpx

from papagui_server.api import create_app
from papagui_server.composition import RuntimeConfiguration, build_container
from papagui_server.domain.models import MutableRunState


TOKEN = "edge-client-token"
def _container(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    return build_container(
        RuntimeConfiguration(
            source_path=source,
            data_path=tmp_path / "data",
            config_path=tmp_path / "config",
            client_token=TOKEN,
        )
    )


def _run(coroutine):
    return asyncio.run(coroutine)


def test_auth_status_lifespan_and_strict_settings_errors(tmp_path: Path) -> None:
    async def scenario() -> None:
        container = _container(tmp_path)
        app = create_app(container, manage_lifecycle=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/v2/server/status")).status_code == 401
            assert (
                await client.get(
                    "/v2/server/status", headers={"Authorization": "Basic xxx"}
                )
            ).status_code == 401
            headers = {"Authorization": f"Bearer {TOKEN}"}
            status = await client.get("/v2/server/status", headers=headers)
            assert status.status_code == 200
            assert status.json()["source_id"] == "primary"
            assert (await client.get("/v2/admin/settings", headers=headers)).status_code == 200
            for invalid in (
                {"settings": []},
                {"settings": {"interval_seconds": "900"}},
                {"settings": {"automatic_runs_enabled": 0}},
                {"settings": {"automatic_runs_enabled": "false"}},
                {"settings": {"minimum_customer_year": True}},
                {"settings": {"unknown": 1}},
            ):
                response = await client.put(
                    "/v2/admin/settings", headers=headers, json=invalid
                )
                assert response.status_code == 400, response.text
                assert response.json()["error"]["code"] == "invalid_request"

            assert (
                await client.post(
                    "/v2/admin/index-runs/current/cancel", headers=headers
                )
            ).status_code == 409
            container.coordinator._state = MutableRunState(
                state="running", run_id="api-running"
            )
            cancelled = await client.post(
                "/v2/admin/index-runs/current/cancel", headers=headers
            )
            assert cancelled.status_code == 202
            container.coordinator._state = MutableRunState()
            assert (
                await client.delete(
                    "/v2/admin/index", headers=headers, params={"rebuild": "false"}
                )
            ).json()["rebuild"] is False
            assert (
                await client.post("/v2/admin/server/restart", headers=headers)
            ).status_code == 202

        lifecycle = create_app(container, manage_lifecycle=True, run_on_start=False)
        async with lifecycle.router.lifespan_context(lifecycle):
            assert container.coordinator._thread is not None
            assert container.recognition_jobs._thread is not None
        assert container.coordinator._thread is None
        assert container.recognition_jobs._thread is None

    _run(scenario())


def test_removed_admin_session_and_generation_http_error_paths(tmp_path: Path) -> None:
    async def scenario() -> None:
        container = _container(tmp_path)
        app = create_app(container, manage_lifecycle=False)
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                    await client.post(
                        "/v2/admin/session", headers=headers, json={"password": "old"}
                    )
                ).status_code == 404
            for url in (
                "/v2/generations/current",
                "/v2/generations/index/missing/manifest",
                "/v2/generations/index/missing/archive",
                "/v2/generations/unknown/missing/archive",
                "/v2/generations/index/../bad/archive",
                "/v1/index/current",
            ):
                response = await client.get(url, headers=headers)
                assert response.status_code in {404, 422}

            index = container.configuration.data_path / "index" / "active.db"
            index.parent.mkdir(parents=True)
            import sqlite3

            connection = sqlite3.connect(index)
            connection.execute("CREATE TABLE sample(value TEXT)")
            connection.commit()
            connection.close()
            container.publisher.publish_index()
            assert (
                await client.get("/v2/generations/current", headers=headers)
            ).status_code == 404

    _run(scenario())


def test_generation_v2_and_v1_golden_download_responses(tmp_path: Path) -> None:
    async def scenario() -> None:
        container = _container(tmp_path)
        project = container.configuration.source_path / "Service" / "2026" / "Gold GmbH"
        project.mkdir(parents=True)
        (project / "gold.txt").write_text("gold", encoding="utf-8")
        assert container.coordinator.run_once(full_rebuild=True) == 0
        app = create_app(container, manage_lifecycle=False)
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            current = (await client.get("/v2/generations/current", headers=headers)).json()
            for kind in ("index", "customers"):
                descriptor = current["components"][kind]
                manifest = await client.get(
                    f"/v2/generations/{kind}/{descriptor['generation']}/manifest",
                    headers=headers,
                )
                assert manifest.json() == descriptor
                archive = await client.get("/" + descriptor["archive"], headers=headers)
                assert archive.status_code == 200
                assert archive.headers["content-type"] == "application/zip"
                assert archive.headers["content-length"] == str(descriptor["size"])
                assert "attachment" in archive.headers["content-disposition"]

            legacy = await client.get("/v1/index/current", headers=headers)
            assert legacy.status_code == 200
            assert set(legacy.json()) == {
                "schema_version",
                "generation",
                "created_at",
                "archive",
                "size",
                "sha256",
            }
            legacy_archive = await client.get(
                "/v1/index/generations/" + legacy.json()["archive"], headers=headers
            )
            assert legacy_archive.status_code == 200
            assert legacy_archive.content

    _run(scenario())


def test_customer_and_journal_http_crud_and_validation(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(_container(tmp_path), manage_lifecycle=False)
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/v2/customers", headers=headers)).json()["customers"] == []
            assert (
                await client.get("/v2/customers", headers=headers, params={"limit": 0})
            ).status_code == 422
            assert (await client.get("/v2/customers/999", headers=headers)).status_code == 404
            assert (
                await client.post("/v2/customers", headers=headers, json={"customer": []})
            ).status_code == 400
            assert (
                await client.post(
                    "/v2/customers",
                    headers=headers,
                    json={"customer": {"display_name": "No Key"}},
                )
            ).status_code == 400
            created_response = await client.post(
                "/v2/customers",
                headers={**headers, "Idempotency-Key": "http-create-0001"},
                json={"customer": {"display_name": "HTTP GmbH"}},
            )
            customer = created_response.json()["customer"]
            assert (await client.get(f"/v2/customers/{customer['id']}", headers=headers)).status_code == 200
            for revision in (True, -1, "invalid"):
                response = await client.put(
                    f"/v2/customers/{customer['id']}",
                    headers={**headers, "Idempotency-Key": f"bad-rev-{str(revision):0<8}"},
                    json={"customer": {"display_name": "Bad"}, "expected_revision": revision},
                )
                assert response.status_code == 400
            updated = await client.put(
                f"/v2/customers/{customer['id']}",
                headers={**headers, "Idempotency-Key": "http-update-0001"},
                json={"customer": {"display_name": "HTTP Neu"}, "expected_revision": 1},
            )
            assert updated.json()["customer"]["revision"] == 2
            assert (
                await client.get(
                    f"/v2/customers/{customer['id']}/journal", headers=headers
                )
            ).json()["revision"] == 2
            added = await client.post(
                f"/v2/customers/{customer['id']}/journal",
                headers={**headers, "Idempotency-Key": "http-journal-add"},
                json={"entry": {"title": "A", "body": "B"}, "expected_revision": 2},
            )
            entry = added.json()["entry"]
            changed = await client.put(
                f"/v2/customers/{customer['id']}/journal/{entry['id']}",
                headers={**headers, "Idempotency-Key": "http-journal-put"},
                json={"entry": {"title": "C", "body": "D"}, "expected_revision": 3},
            )
            assert changed.json()["entry"]["title"] == "C"
            removed = await client.delete(
                f"/v2/customers/{customer['id']}/journal/{entry['id']}",
                headers={
                    **headers,
                    "Idempotency-Key": "http-journal-del",
                    "If-Match": '"4"',
                },
            )
            assert removed.json()["revision"] == 5
            deleted = await client.delete(
                f"/v2/customers/{customer['id']}",
                headers={
                    **headers,
                    "Idempotency-Key": "http-delete-0001",
                    "If-Match": "5",
                },
            )
            assert deleted.json()["deleted"]

    _run(scenario())


def test_mutation_envelopes_cover_customer_and_journal_dispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(_container(tmp_path), manage_lifecycle=False)
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/v2/customer-mutations",
                headers=headers,
                json={
                    "operation": "create",
                    "target": "customer",
                    "idempotency_key": "envelope-create-01",
                    "payload": {"display_name": "Envelope GmbH"},
                },
            )
            customer = created.json()["customer"]
            updated = await client.post(
                "/v2/customer-mutations",
                headers=headers,
                json={
                    "operation": "update",
                    "target": "customer",
                    "idempotency_key": "envelope-update-01",
                    "target_id": customer["id"],
                    "expected_revision": 1,
                    "payload": {**customer, "display_name": "Envelope Neu"},
                },
            )
            assert updated.json()["customer"]["revision"] == 2
            missing_revision = await client.post(
                "/v2/customer-mutations",
                headers=headers,
                json={
                    "operation": "create",
                    "target": "journal",
                    "idempotency_key": "envelope-journal-0",
                    "customer_id": customer["id"],
                    "payload": {"title": "x", "body": "x"},
                },
            )
            assert missing_revision.status_code == 400
            journal = await client.post(
                "/v2/customer-mutations",
                headers=headers,
                json={
                    "operation": "create",
                    "target": "journal",
                    "idempotency_key": "envelope-journal-1",
                    "customer_id": customer["id"],
                    "expected_revision": 2,
                    "payload": {"title": "one", "body": "body"},
                },
            )
            entry = journal.json()["entry"]
            journal_update = await client.post(
                "/v2/customer-mutations",
                headers=headers,
                json={
                    "operation": "update",
                    "target": "journal",
                    "idempotency_key": "envelope-journal-2",
                    "customer_id": customer["id"],
                    "target_id": entry["id"],
                    "expected_revision": 3,
                    "payload": {"title": "two", "body": "body"},
                },
            )
            assert journal_update.json()["revision"] == 4
            journal_delete = await client.post(
                "/v2/customer-mutations",
                headers=headers,
                json={
                    "operation": "delete",
                    "target": "journal",
                    "idempotency_key": "envelope-journal-3",
                    "customer_id": customer["id"],
                    "target_id": entry["id"],
                    "expected_revision": 4,
                },
            )
            assert journal_delete.json()["revision"] == 5
            customer_delete = await client.post(
                "/v2/customer-mutations",
                headers=headers,
                json={
                    "operation": "delete",
                    "target": "customer",
                    "idempotency_key": "envelope-delete-01",
                    "target_id": customer["id"],
                    "expected_revision": 5,
                },
            )
            assert customer_delete.json()["deleted"]

    _run(scenario())


def test_v1_golden_status_settings_actions_and_customer_crud(tmp_path: Path) -> None:
    async def scenario() -> None:
        container = _container(tmp_path)
        app = create_app(container, manage_lifecycle=False)
        client_headers = {"Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            status = await client.get("/v1/server/status", headers=client_headers)
            assert status.status_code == 200
            assert set(status.json()) == {"server", "job", "generation", "backups"}
            assert set(status.json()["job"]) == {
                "status",
                "job_id",
                "phase",
                "processed_count",
                "current_path",
                "error",
            }
            settings = await client.get("/v1/server/settings", headers=client_headers)
            assert settings.json()["settings"]["automatic_monitoring_enabled"] is True
            changed = await client.put(
                "/v1/server/settings",
                headers=client_headers,
                json={"settings": {"automatic_monitoring_enabled": False}},
            )
            assert changed.json()["settings"]["automatic_runs_enabled"] is False

            container.index_admin.start = MethodType(
                lambda _self, full_rebuild=False: {
                    "accepted": True,
                    "full": full_rebuild,
                },
                container.index_admin,
            )
            container.index_admin.delete = MethodType(
                lambda _self, rebuild=True: {"accepted": True, "rebuild": rebuild},
                container.index_admin,
            )
            container.index_admin.cancel = MethodType(
                lambda _self: {"accepted": True, "cancelled": True},
                container.index_admin,
            )
            container.index_admin.restart = MethodType(
                lambda _self: {"accepted": True, "restart": True},
                container.index_admin,
            )
            for action in ("run", "rebuild", "delete", "cancel", "restart", "interval:900"):
                response = await client.post(
                    "/v1/server/actions", headers=client_headers, json={"action": action}
                )
                assert response.status_code == 202
            assert (
                await client.post(
                    "/v1/server/actions", headers=client_headers, json={"action": "unknown"}
                )
            ).status_code == 400

            created = await client.post(
                "/v1/customers",
                headers=client_headers,
                json={"customer": {"display_name": "Legacy GmbH"}},
            )
            customer = created.json()["customer"]
            assert (
                await client.get(
                    f"/v1/customers/{customer['id']}", headers=client_headers
                )
            ).status_code == 200
            updated = await client.put(
                f"/v1/customers/{customer['id']}",
                headers=client_headers,
                json={
                    "customer": {**customer, "display_name": "Legacy Neu"},
                    "expected_revision": 1,
                },
            )
            assert updated.json()["customer"]["revision"] == 2
            deleted = await client.delete(
                f"/v1/customers/{customer['id']}",
                headers={**client_headers, "If-Match": "2"},
            )
            assert deleted.json()["deleted"]

    _run(scenario())


def test_v1_writes_require_only_the_client_token(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        container = _container(tmp_path)
        app = create_app(container, manage_lifecycle=False)
        client_headers = {"Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Read-only compatibility remains available to an authenticated client.
            assert (
                await client.get("/v1/server/status", headers=client_headers)
            ).status_code == 200
            assert (
                await client.get("/v1/server/settings", headers=client_headers)
            ).status_code == 200

            assert (
                await client.put(
                    "/v1/server/settings",
                    json={"settings": {"interval_seconds": 900}},
                )
            ).status_code == 401
            assert (
                await client.post(
                    "/v1/server/actions", json={"action": "interval:900"}
                )
            ).status_code == 401
            assert (
                await client.delete(
                    "/v1/customers/1", headers={"If-Match": "1"}
                )
            ).status_code == 401
            assert (
                await client.put(
                    "/v1/server/settings",
                    headers=client_headers,
                    json={"settings": {"interval_seconds": 900}},
                )
            ).status_code == 200

    _run(scenario())
