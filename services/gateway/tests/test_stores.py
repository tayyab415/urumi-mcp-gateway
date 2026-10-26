"""Provisioned stores: the gateway registers each Ready store's MCP server by
itself, and removes it with the store. The provisioner is faked; its real
Kubernetes work is tested on the cluster (docs/multi-store.md)."""

from __future__ import annotations

import dataclasses

import anyio
import httpx2
import pytest
from fastapi import FastAPI, HTTPException

from gateway.config import Settings
from gateway.main import create_app
from tests.test_gateway import CSRF, http_app, login, make_alpha, mcp_client, serving

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def fake_provisioner(mcp_url: str) -> tuple[FastAPI, dict]:
    stores: dict[str, dict] = {}
    app = FastAPI()

    @app.get("/stores")
    async def list_stores():
        for s in stores.values():
            if s["phase"] == "Provisioning":
                s["phase"] = "Ready"  # ready on the next look, for the test
        return list(stores.values())

    @app.put("/stores/{slug}")
    async def put(slug: str, body: dict):
        if slug in stores:
            return stores[slug]
        if sum(1 for s in stores.values() if s["owner_id"] == body["owner_id"]) >= 1:
            raise HTTPException(403, "You already have 1 stores, the most one person may have. Delete one first.")
        stores[slug] = {"slug": slug, "name": body["name"], "owner_id": body["owner_id"], "owner_email": body["owner_email"],
                        "phase": "Provisioning", "reason": "", "url": f"http://{slug}.example", "mcp_url": mcp_url,
                        "created_at": "2026-10-26T00:00:00Z", "ready_at": None, "namespace": f"store-{slug}"}
        return stores[slug]

    @app.delete("/stores/{slug}")
    async def delete(slug: str):
        stores[slug]["phase"] = "Deleting"
        return stores[slug]

    return app, stores


async def test_store_lifecycle_registers_and_removes_tools(tmp_path):
    base = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db", secret_key="test-secret-key-0123456789",
        bootstrap_admin_email="admin@example.com", bootstrap_admin_password="admin-password-1",
        public_url="http://gateway.test", cookie_secure=False, session_hours=1, refresh_interval_seconds=0,
        default_timeout_seconds=5, allow_loopback_upstreams=True,
    )
    async with anyio.create_task_group() as tg:
        async with serving(tg, http_app(make_alpha())) as (alpha_url, _):
            prov_app, stores = fake_provisioner(f"{alpha_url}/mcp")
            async with serving(tg, prov_app) as (prov_url, _):
                settings = dataclasses.replace(base, provisioner_url=prov_url, provisioner_token="t" * 20,
                                               store_sync_interval_seconds=0.2)
                async with (
                    serving(tg, create_app(settings)) as (gw, _),
                    httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin,
                    httpx2.AsyncClient(base_url=gw, headers=CSRF) as ana,
                    httpx2.AsyncClient(base_url=gw, headers=CSRF) as bob,
                ):
                    await login(admin, "admin@example.com", "admin-password-1")
                    for who, email in ((ana, "ana@example.com"), (bob, "bob@example.com")):
                        invite = (await admin.post("/api/invites", json={"email": email, "role": "member"})).json()
                        token = invite["invite_url"].split("token=")[1]
                        r = await who.post(f"/api/invites/accept/{token}", json={"name": email[:3], "password": "member-password-1"})
                        assert r.status_code == 200
                    ana_key = (await ana.post("/api/keys", json={"name": "cli"})).json()["key"]

                    # A member creates a store; asking again is harmless; a second one hits her limit.
                    r = await ana.post("/api/stores", json={"slug": "north", "name": "North"})
                    assert r.status_code == 200 and r.json()["phase"] == "Provisioning", r.text
                    assert (await ana.post("/api/stores", json={"slug": "north", "name": "North"})).status_code == 200
                    r = await ana.post("/api/stores", json={"slug": "south", "name": "South"})
                    assert r.status_code == 403 and "most one person" in r.json()["detail"]

                    # Once Ready, the gateway registers its MCP server; tool names say which store.
                    async with mcp_client(gw, ana_key) as client:
                        with anyio.fail_after(10):
                            while "store-north__list_products" not in {t.name for t in (await client.list_tools()).tools}:
                                await anyio.sleep(0.2)
                        result = await client.call_tool("store-north__list_products", {})
                        assert not result.is_error

                        # Only the owner (and admins) see a store's tools; Bob cannot call them either.
                        bob_key = (await bob.post("/api/keys", json={"name": "cli"})).json()["key"]
                        async with mcp_client(gw, bob_key) as bob_mcp:
                            assert not any(t.name.startswith("store-north__") for t in (await bob_mcp.list_tools()).tools)
                            hidden = await bob_mcp.call_tool("store-north__list_products", {})
                            assert hidden.is_error and "Unknown tool" in hidden.content[0].text

                        listed = (await ana.get("/api/stores")).json()["stores"]
                        assert listed[0]["registered"] and listed[0]["mine"] and listed[0]["can_delete"]
                        assert (await bob.get("/api/stores")).json()["stores"][0]["can_delete"] is False

                        # Admins cannot remove a store's server by hand; members cannot delete others' stores.
                        servers = (await admin.get("/api/servers")).json()
                        north = next(s for s in servers if s["slug"] == "store-north")
                        assert north["managed_by"] == "provisioner"
                        assert (await admin.delete(f"/api/servers/{north['id']}")).status_code == 409
                        assert (await bob.delete("/api/stores/north")).status_code == 403

                        # The owner deletes it: its tools are gone at once, and stay gone.
                        r = await ana.delete("/api/stores/north")
                        assert r.status_code == 200 and r.json()["phase"] == "Deleting"
                        assert not any(t.name.startswith("store-north__") for t in (await client.list_tools()).tools)
                        await anyio.sleep(0.6)  # a few sync passes
                        assert not any(t.name.startswith("store-north__") for t in (await client.list_tools()).tools)
                        gone = await client.call_tool("store-north__list_products", {})
                        assert gone.is_error and "Unknown tool" in gone.content[0].text
                    assert stores["north"]["phase"] == "Deleting"
                tg.cancel_scope.cancel()
