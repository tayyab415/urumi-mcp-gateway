"""Search mode over real MCP: two tools listed, same permissions and audit as direct calls."""

from __future__ import annotations

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from gateway.config import Settings
from gateway.main import create_app
from gateway.toolsearch import rank
from tests.test_gateway import CSRF, http_app, login, make_alpha, make_beta, serving

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_rank_prefers_the_matching_tool():
    docs = [("woo__list_orders", "woo list orders List the store's orders"),
            ("woo__list_products", "woo list products List products"),
            ("shipping__quote_shipping", "shipping quote shipping Quote a delivery price")]
    assert rank("today's orders", docs, 3)[0][0] == "woo__list_orders"
    assert rank("delivery price", docs, 3)[0][0] == "shipping__quote_shipping"
    assert rank("zzz", docs, 3) == []


async def test_search_mode(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db", secret_key="test-secret-key-0123456789",
        bootstrap_admin_email="admin@example.com", bootstrap_admin_password="admin-password-1",
        public_url="http://gateway.test", cookie_secure=False, session_hours=1, refresh_interval_seconds=0,
        default_timeout_seconds=5, allow_loopback_upstreams=True,
    )
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, http_app(make_alpha())) as (alpha_url, _),
            serving(tg, http_app(make_beta())) as (beta_url, _),
            serving(tg, create_app(settings)) as (gw, _),
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin,
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as member,
        ):
            await login(admin, "admin@example.com", "admin-password-1")
            for slug, url in (("alpha", alpha_url), ("beta", beta_url)):
                assert (await admin.post("/api/servers", json={"slug": slug, "name": slug.title(), "url": f"{url}/mcp"})).status_code == 201
            assert (await admin.post("/api/servers", json={"slug": "gateway", "name": "x", "url": f"{alpha_url}/mcp"})).status_code == 422
            invite = (await admin.post("/api/invites", json={"email": "bob@example.com", "role": "member"})).json()
            await member.post(f"/api/invites/accept/{invite['invite_url'].split('token=')[1]}", json={"name": "Bob", "password": "bob-password-1"})
            key = (await member.post("/api/keys", json={"name": "cli"})).json()["key"]

            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30) as http:
                async with Client(streamable_http_client(f"{gw}/mcp?tools=search", http_client=http), cache=None) as bob:
                    listed = [t.name for t in (await bob.list_tools()).tools]
                    assert listed == ["gateway__search_tools", "gateway__call_tool"]

                    found = await bob.call_tool("gateway__search_tools", {"query": "products"})
                    names = [m["name"] for m in found.structured_content["matches"]]
                    assert set(names[:2]) == {"alpha__list_products", "beta__list_products"}
                    # Members never find the write tool they could not call directly.
                    everything = await bob.call_tool("gateway__search_tools", {"query": "create thing something", "limit": 20})
                    assert "alpha__create_thing" not in str(everything.structured_content)

                    ok = await bob.call_tool("gateway__call_tool", {"name": "beta__list_products", "arguments": {}})
                    assert not ok.is_error and "supplier ball" in str(ok.structured_content or ok.content)
                    denied = await bob.call_tool("gateway__call_tool", {"name": "alpha__create_thing", "arguments": {"name": "x"}})
                    assert denied.is_error and "not allowed" in denied.content[0].text

            audit = (await admin.get("/api/audit")).json()
            rows = {(e["public_name"], e["outcome"], e["via"]) for e in audit}
            assert ("beta__list_products", "ok", "mcp-search") in rows
            assert ("alpha__create_thing", "denied", "mcp-search") in rows
            tg.cancel_scope.cancel()
