from __future__ import annotations

import asyncio

import httpx

from papagui_server.api import create_app
from tests.server.test_api import _container


def test_update_hold_blocks_clients_until_validation_commits(tmp_path, monkeypatch):
    hold = tmp_path / "update-hold"
    hold.touch()
    monkeypatch.setenv("PAPAGUI_UPDATE_HOLD_FILE", str(hold))
    app = create_app(_container(tmp_path), manage_lifecycle=False)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            blocked = await client.get("/v2/system/info", headers={"Authorization": "Bearer client-token-123"})
            assert blocked.status_code == 503
            assert blocked.headers["Retry-After"] == "30"
            assert (await client.post("/v2/customer-mutations", json={})).status_code == 503
            hold.unlink()
            assert (await client.get("/v2/system/info", headers={"Authorization": "Bearer client-token-123"})).status_code == 200
    asyncio.run(scenario())
