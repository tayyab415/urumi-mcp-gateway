"""The gateway's required behaviors, end to end, over real HTTP.

Two small upstream MCP servers and the gateway run under uvicorn on local
ports. An admin and a member use the dashboard API and real MCP clients.
"""

from __future__ import annotations

import dataclasses
import json
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
import mcp_types as types
from mcp_types import ToolAnnotations
from fastapi import HTTPException
from starlette.responses import JSONResponse

from gateway.config import Settings
from gateway.core import Gateway
from gateway.db import Database
from gateway.main import bootstrap_servers, create_app
from gateway.mcp_endpoint import NOT_AUTHENTICATED
from gateway.security import CredentialBox

pytestmark = pytest.mark.anyio
READ_ONLY = ToolAnnotations(read_only_hint=True)
CSRF = {"X-Urumi-CSRF": "1"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------- upstreams


def make_alpha() -> MCPServer:
    alpha = MCPServer("alpha")

    @alpha.tool(annotations=READ_ONLY)
    def list_products() -> list[str]:
        """Alpha's products."""
        return ["ball"]

    @alpha.tool()
    def create_thing(name: str) -> str:
        """Create something (a write)."""
        return f"created {name}"

    return alpha


def make_beta() -> MCPServer:
    beta = MCPServer("beta")

    @beta.tool(annotations=READ_ONLY)
    def list_products() -> list[str]:
        """Beta's products: same tool name as alpha."""
        return ["supplier ball"]

    @beta.tool(annotations=READ_ONLY)
    async def slow(seconds: float) -> str:
        """Sleep, then answer."""
        await anyio.sleep(seconds)
        return "done"

    return beta


def http_app(server: MCPServer, bearer: str | None = None):
    inner = server.streamable_http_app(
        stateless_http=True, json_response=True, host="0.0.0.0",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def app(scope, receive, send):
        if bearer and scope["type"] == "http":
            headers = dict(scope["headers"])
            if headers.get(b"authorization") != f"Bearer {bearer}".encode():
                await JSONResponse({"error": "unauthorized"}, 401)(scope, receive, send)
                return
        await inner(scope, receive, send)

    return app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def serving(tg: anyio.abc.TaskGroup, app: Any, port: int | None = None) -> AsyncIterator[tuple[str, uvicorn.Server]]:
    port = port or free_port()  # a test passes the port when the app must know its own public URL
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    tg.start_soon(server.serve)
    with anyio.fail_after(10):
        while not server.started:
            await anyio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}", server
    finally:
        server.should_exit = True


# ------------------------------------------------------------------ helpers


@asynccontextmanager
async def mcp_client(base: str, key: str) -> AsyncIterator[Client]:
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30) as http:
        async with Client(streamable_http_client(f"{base}/mcp", http_client=http), cache=None) as client:
            yield client


async def login(http: httpx2.AsyncClient, email: str, password: str) -> None:
    r = await http.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------- test


async def test_gateway_definition_of_done(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db",
        secret_key="test-secret-key-0123456789",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="admin-password-1",
        public_url="http://gateway.test",
        cookie_secure=False,
        session_hours=1,
        refresh_interval_seconds=0,
        default_timeout_seconds=5,
        allow_loopback_upstreams=True,
    )
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, http_app(make_alpha())) as (alpha_url, alpha_server),
            serving(tg, http_app(make_beta(), bearer="beta-token")) as (beta_url, _),
            serving(tg, create_app(settings)) as (gw, _),
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin,
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as member,
        ):
            # --- admin registers two upstreams; beta needs a bearer credential
            await login(admin, "admin@example.com", "admin-password-1")
            r = await admin.post("/api/servers", json={"slug": "alpha", "name": "Alpha", "url": f"{alpha_url}/mcp", "timeout_seconds": 5})
            assert r.status_code == 201 and r.json()["status"] == "ok", r.text
            alpha_id = r.json()["id"]
            r = await admin.post("/api/servers", json={"slug": "beta", "name": "Beta", "url": f"{beta_url}/mcp",
                                                       "auth_type": "bearer", "credential": "beta-token", "timeout_seconds": 1})
            beta = r.json()
            assert beta["status"] == "ok" and beta["has_credential"] is True
            assert "beta-token" not in r.text  # the credential never comes back

            # --- a wrong credential shows up as auth_failed, not as "down"
            r = await admin.patch(f"/api/servers/{beta['id']}", json={"credential": "wrong"})
            assert r.json()["status"] == "auth_failed"
            await admin.patch(f"/api/servers/{beta['id']}", json={"credential": "beta-token"})

            # --- invite a member; they accept and make an API key
            invite = (await admin.post("/api/invites", json={"email": "bob@example.com", "role": "member"})).json()
            token = invite["invite_url"].split("token=")[1]
            r = await member.post(f"/api/invites/accept/{token}", json={"name": "Bob", "password": "bob-password-1"})
            assert r.status_code == 200 and r.json()["role"] == "member"
            member_key = (await member.post("/api/keys", json={"name": "laptop"})).json()
            admin_key = (await admin.post("/api/keys", json={"name": "admin cli"})).json()["key"]

            # --- members cannot administer
            assert (await member.post("/api/servers", json={"slug": "x", "name": "x", "url": f"{alpha_url}/mcp"})).status_code == 403
            assert (await member.get("/api/users")).status_code == 403

            async with mcp_client(gw, member_key["key"]) as bob, mcp_client(gw, admin_key) as root:
                # --- one endpoint, both upstreams, colliding names kept apart, write tool hidden from members
                bob_tools = {t.name for t in (await bob.list_tools()).tools}
                assert bob_tools == {"alpha__list_products", "beta__list_products", "beta__slow"}
                root_tools = {t.name for t in (await root.list_tools()).tools}
                assert "alpha__create_thing" in root_tools

                a = await bob.call_tool("alpha__list_products", {})
                b = await bob.call_tool("beta__list_products", {})
                assert "ball" in str(a.structured_content) and "supplier ball" in str(b.structured_content)

                # --- calling a hidden tool directly is still refused, with the JSON-RPC error an
                # unknown tool gets (MCP spec: -32602), so the answer does not say the tool exists
                with pytest.raises(MCPError) as denied:
                    await bob.call_tool("alpha__create_thing", {"name": "x"})
                with pytest.raises(MCPError) as unknown:
                    await bob.call_tool("alpha__no_such_tool", {})
                assert denied.value.code == unknown.value.code == types.INVALID_PARAMS
                assert denied.value.message == "Unknown tool: alpha__create_thing"
                assert unknown.value.message == "Unknown tool: alpha__no_such_tool"
                assert not (await root.call_tool("alpha__create_thing", {"name": "x"})).is_error

                # On the wire (a bare request, as curl sends it): an "error" member, not a result with isError.
                raw = await httpx2.AsyncClient().post(
                    f"{gw}/mcp", headers={"Authorization": f"Bearer {member_key['key']}", "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "review__no_such_tool", "arguments": {}}},
                )
                assert "result" not in raw.json(), raw.text
                assert raw.json()["error"]["code"] == -32602 and raw.json()["error"]["message"] == "Unknown tool: review__no_such_tool"

                # --- a slow upstream times out without holding up a healthy one
                async def slow_call(results: dict) -> None:
                    t = time.monotonic()
                    results["slow"] = await bob.call_tool("beta__slow", {"seconds": 5})
                    results["slow_s"] = time.monotonic() - t

                results: dict = {}
                async with anyio.create_task_group() as calls:
                    calls.start_soon(slow_call, results)
                    await anyio.sleep(0.1)
                    fast = await bob.call_tool("alpha__list_products", {})
                    slow_still_pending = "slow" not in results
                # The healthy upstream answered while the slow one was still hanging...
                assert not fast.is_error and slow_still_pending
                # ...and the slow one was cut off by its 1 s timeout, long before its 5 s sleep ended.
                assert results["slow"].is_error and "did not answer within 1s" in results["slow"].content[0].text
                assert results["slow_s"] < 4

                # --- disabling a server removes its tools and rejects stale calls
                await admin.patch(f"/api/servers/{beta['id']}", json={"enabled": False})
                assert {t.name for t in (await bob.list_tools()).tools} == {"alpha__list_products"}
                stale = await bob.call_tool("beta__list_products", {})
                assert stale.is_error and "disabled" in stale.content[0].text
                await admin.patch(f"/api/servers/{beta['id']}", json={"enabled": True})

                # --- admin narrows a tool to admins only; members lose it at once
                await admin.patch(f"/api/servers/{beta['id']}/tools/slow", json={"allowed_roles": ["admin"]})
                assert "beta__slow" not in {t.name for t in (await bob.list_tools()).tools}

                # --- a dead upstream fails fast and clearly; discovery is unaffected
                alpha_server.should_exit = True
                await anyio.sleep(0.5)
                t = time.monotonic()
                dead = await bob.call_tool("alpha__list_products", {})
                assert dead.is_error and "unreachable" in dead.content[0].text and time.monotonic() - t < 3
                assert "beta__list_products" in {t.name for t in (await bob.list_tools()).tools}
                servers = {s["slug"]: s for s in (await admin.get("/api/servers")).json()}
                assert servers["alpha"]["status"] == "down" and servers["beta"]["status"] == "ok"

                # --- revoking the key rejects the very next call on the same open client
                assert (await admin.delete(f"/api/keys/{member_key['id']}")).status_code == 200
                with pytest.raises(Exception):
                    await bob.call_tool("beta__list_products", {})

            # --- requests without a key never reach the MCP layer
            raw = await httpx2.AsyncClient().post(f"{gw}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            assert raw.status_code == 401
            # Not -32001, which MCP clients read as "request timed out" (outcome unknown).
            assert raw.json()["error"]["code"] == NOT_AUTHENTICATED != types.REQUEST_TIMEOUT

            # --- every attempt is in the audit log under the right person
            events = (await admin.get("/api/audit", params={"limit": 500})).json()
            by_outcome = {(e["user_email"], e["public_name"], e["outcome"]) for e in events}
            assert ("bob@example.com", "alpha__create_thing", "denied") in by_outcome
            assert ("bob@example.com", "alpha__no_such_tool", "unknown_tool") in by_outcome
            assert ("bob@example.com", "review__no_such_tool", "unknown_tool") in by_outcome
            assert ("admin@example.com", "alpha__create_thing", "ok") in by_outcome
            assert ("bob@example.com", "beta__slow", "timeout") in by_outcome
            assert ("bob@example.com", "beta__list_products", "disabled") in by_outcome
            assert ("bob@example.com", "alpha__list_products", "unreachable") in by_outcome
            assert all(e["api_key_prefix"] for e in events if e["via"] == "mcp")
            # members only see their own rows
            own = (await member.get("/api/audit")).json()
            assert own and {e["user_email"] for e in own} == {"bob@example.com"}

            tg.cancel_scope.cancel()


async def test_chat_token_follows_the_login_session(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db", secret_key="test-secret-key-0123456789",
        bootstrap_admin_email="admin@example.com", bootstrap_admin_password="admin-password-1",
        public_url="http://gateway.test", cookie_secure=False, session_hours=1, refresh_interval_seconds=0,
        default_timeout_seconds=5, allow_loopback_upstreams=True,
    )
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, http_app(make_alpha())) as (alpha_url, _),
            serving(tg, create_app(settings)) as (gw, _),
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin,
        ):
            await login(admin, "admin@example.com", "admin-password-1")
            await admin.post("/api/servers", json={"slug": "alpha", "name": "Alpha", "url": f"{alpha_url}/mcp"})
            token = (await admin.post("/api/auth/chat-token")).json()["token"]
            assert token == (await admin.post("/api/auth/chat-token")).json()["token"]  # stable per session
            assert (await admin.post("/api/auth/chat-token", headers={"X-Urumi-CSRF": ""})).status_code == 403

            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}", "X-Urumi-Chat-Id": "conv-1"}) as http:
                async with Client(streamable_http_client(f"{gw}/mcp", http_client=http), cache=None) as chat:
                    assert not (await chat.call_tool("alpha__create_thing", {"name": "y"})).is_error
                    events = (await admin.get("/api/audit")).json()
                    assert events[0]["via"] == "chat" and events[0]["chat_id"] == "conv-1"
                    assert events[0]["user_email"] == "admin@example.com"

                    forged = token[:-4] + "AAAA"
                    r = await httpx2.AsyncClient().post(f"{gw}/mcp", headers={"Authorization": f"Bearer {forged}"},
                                                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                    assert r.status_code == 401

                    await admin.post("/api/auth/logout")
                    with pytest.raises(Exception):
                        await chat.call_tool("alpha__list_products", {})
            tg.cancel_scope.cancel()


async def test_dashboard_helpers_test_connection_and_audit_views(tmp_path):
    """The add-server test button, live audit polling, per-chat audit, and the overview summary."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db", secret_key="test-secret-key-0123456789",
        bootstrap_admin_email="admin@example.com", bootstrap_admin_password="admin-password-1",
        public_url="http://gateway.test", cookie_secure=False, session_hours=1, refresh_interval_seconds=0,
        default_timeout_seconds=5, allow_loopback_upstreams=True,
    )
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, http_app(make_alpha())) as (alpha_url, _),
            serving(tg, http_app(make_beta(), bearer="beta-token")) as (beta_url, _),
            serving(tg, create_app(settings)) as (gw, _),
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin,
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as member,
        ):
            await login(admin, "admin@example.com", "admin-password-1")
            await admin.post("/api/servers", json={"slug": "alpha", "name": "Alpha", "url": f"{alpha_url}/mcp"})

            # --- test connection: nothing saved, collision with alpha's list_products reported
            r = await admin.post("/api/servers/test", json={"slug": "beta", "url": f"{beta_url}/mcp",
                                                            "auth_type": "bearer", "credential": "beta-token"})
            assert r.status_code == 200, r.text
            probe = r.json()
            assert probe["ok"] is True and probe["latency_ms"] >= 0 and probe["protocol_version"]
            assert {t["public_name"] for t in probe["tools"]} == {"beta__list_products", "beta__slow"}
            assert all(t["read_only"] for t in probe["tools"])
            assert probe["collisions"] == [{"name": "list_products", "servers": ["alpha"]}]
            assert {s["slug"] for s in (await admin.get("/api/servers")).json()} == {"alpha"}

            # --- a wrong credential and a dead address come back as plain failures, not HTTP errors
            r = await admin.post("/api/servers/test", json={"slug": "beta", "url": f"{beta_url}/mcp",
                                                            "auth_type": "bearer", "credential": "wrong"})
            assert r.json()["ok"] is False and r.json()["kind"] == "auth_failed"
            r = await admin.post("/api/servers/test", json={"slug": "ghost", "url": f"http://127.0.0.1:{free_port()}/mcp"})
            assert r.json()["ok"] is False and r.json()["kind"] == "unreachable" and "unreachable" in r.json()["message"]
            # validation still refuses what save would refuse
            assert (await admin.post("/api/servers/test", json={"slug": "beta", "url": "ftp://x/mcp"})).status_code == 422
            assert (await admin.post("/api/servers/test", json={"slug": "Beta", "url": f"{beta_url}/mcp"})).status_code == 422
            assert (await admin.post("/api/servers/test", json={"slug": "beta", "url": f"{beta_url}/mcp",
                                                                 "auth_type": "header"})).status_code == 422

            # --- editing: a blank credential reuses the stored one for that server
            beta = (await admin.post("/api/servers", json={"slug": "beta", "name": "Beta", "url": f"{beta_url}/mcp",
                                                           "auth_type": "bearer", "credential": "beta-token"})).json()
            r = await admin.post("/api/servers/test", json={"slug": "beta", "url": f"{beta_url}/mcp",
                                                            "auth_type": "bearer", "server_id": beta["id"]})
            assert r.json()["ok"] is True
            assert r.json()["collisions"] == [{"name": "list_products", "servers": ["alpha"]}]  # beta itself is excluded
            assert (await member.post("/api/servers/test", json={"slug": "x", "url": f"{beta_url}/mcp"})).status_code == 401

            # --- make some calls, two of them under a chat id
            admin_key = (await admin.post("/api/keys", json={"name": "cli"})).json()["key"]
            async with mcp_client(gw, admin_key) as root:
                await root.call_tool("alpha__list_products", {})
                await root.call_tool("alpha__create_thing", {"name": "x"})
                with pytest.raises(MCPError):
                    await root.call_tool("nope__missing", {})
            token = (await admin.post("/api/auth/chat-token")).json()["token"]
            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}", "X-Urumi-Chat-Id": "conv-9"}) as http:
                async with Client(streamable_http_client(f"{gw}/mcp", http_client=http), cache=None) as chat:
                    await chat.call_tool("alpha__list_products", {})
                    await chat.call_tool("beta__list_products", {})

            # --- after_id: only the rows newer than what the page already has, newest first
            events = (await admin.get("/api/audit")).json()
            assert len(events) == 5 and events[0]["id"] > events[-1]["id"]
            newer = (await admin.get("/api/audit", params={"after_id": events[2]["id"]})).json()
            assert [e["id"] for e in newer] == [events[0]["id"], events[1]["id"]]
            assert (await admin.get("/api/audit", params={"after_id": events[0]["id"]})).json() == []

            # --- chat_id: the "Open run" view
            run = (await admin.get("/api/audit", params={"chat_id": "conv-9"})).json()
            assert [e["public_name"] for e in run] == ["beta__list_products", "alpha__list_products"]
            assert all(e["via"] == "chat" and e["chat_id"] == "conv-9" for e in run)

            # --- summary: per server, per hour, slowest tools
            summary = (await admin.get("/api/audit/summary", params={"hours": 6})).json()
            servers = {s["slug"]: s for s in summary["servers"]}
            assert servers["alpha"]["calls"] == 3 and servers["alpha"]["errors"] == 0
            assert servers["beta"]["calls"] == 1 and servers["nope"]["errors"] == 1
            assert isinstance(servers["alpha"]["p50_ms"], int) and servers["alpha"]["p95_ms"] >= servers["alpha"]["p50_ms"]
            assert len(summary["per_hour"]) == 7 and sum(h["calls"] for h in summary["per_hour"]) == 5
            assert sum(h["errors"] for h in summary["per_hour"]) == 1
            assert summary["per_hour"][0]["hour"] < summary["per_hour"][-1]["hour"]  # oldest first
            assert summary["since"].endswith(":00:00Z")
            slowest = summary["slowest_tools"]
            assert 1 <= len(slowest) <= 5 and {"public_name", "p95_ms", "calls"} <= set(slowest[0])
            assert [t["p95_ms"] for t in slowest] == sorted((t["p95_ms"] for t in slowest), reverse=True)
            assert (await admin.get("/api/audit/summary", params={"hours": 0})).status_code == 422

            # members see only their own rows in both views
            invite = (await admin.post("/api/invites", json={"email": "bob@example.com", "role": "member"})).json()
            await member.post(f"/api/invites/accept/{invite['invite_url'].split('token=')[1]}",
                              json={"name": "Bob", "password": "bob-password-1"})
            assert (await member.get("/api/audit", params={"chat_id": "conv-9"})).json() == []
            empty = (await member.get("/api/audit/summary")).json()
            assert empty["servers"] == [] and empty["slowest_tools"] == [] and len(empty["per_hour"]) == 25

            tg.cancel_scope.cancel()


async def test_bootstrap_servers_register_once(tmp_path, monkeypatch):
    """A fresh install lists its first servers; after that the database decides."""
    monkeypatch.setenv("BETA_TOKEN", "beta-token")
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, http_app(make_alpha())) as (alpha_url, _),
            serving(tg, http_app(make_beta(), bearer="beta-token")) as (beta_url, _),
        ):
            servers_file = tmp_path / "servers.json"
            servers_file.write_text(json.dumps([
                {"slug": "alpha", "name": "Alpha", "url": f"{alpha_url}/mcp"},
                {"slug": "beta", "name": "Beta", "url": f"{beta_url}/mcp",
                 "auth_type": "bearer", "credential_env": "BETA_TOKEN", "timeout_seconds": 5},
            ]))
            settings = Settings(
                database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db", secret_key="test-secret-key-0123456789",
                bootstrap_admin_email="admin@example.com", bootstrap_admin_password="admin-password-1",
                public_url="http://gateway.test", cookie_secure=False, session_hours=1, refresh_interval_seconds=0,
                default_timeout_seconds=5, allow_loopback_upstreams=True, bootstrap_servers_file=str(servers_file),
            )

            async with serving(tg, create_app(settings)) as (gw, _), httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin:
                await login(admin, "admin@example.com", "admin-password-1")
                servers = {s["slug"]: s for s in (await admin.get("/api/servers")).json()}
                assert set(servers) == {"alpha", "beta"}
                assert servers["beta"]["has_credential"] and servers["beta"]["auth_type"] == "bearer"
                # The sealed credential opens the upstream, so beta's tools are found.
                r = await admin.post(f"/api/servers/{servers['beta']['id']}/refresh")
                assert r.json()["status"] == "ok" and r.json()["tool_count"] == 2
                assert (await admin.delete(f"/api/servers/{servers['alpha']['id']}")).status_code in (200, 204)

            # A restart does not bring back the server the admin removed.
            async with serving(tg, create_app(settings)) as (gw, _), httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin:
                await login(admin, "admin@example.com", "admin-password-1")
                assert {s["slug"] for s in (await admin.get("/api/servers")).json()} == {"beta"}

            fresh = dataclasses.replace(settings, database_url=f"sqlite+aiosqlite:///{tmp_path}/fresh.db")
            db = Database(fresh.database_url)
            await db.create_all()
            gateway = Gateway(db, CredentialBox(fresh.secret_key))

            # A credential variable that is missing stops startup.
            monkeypatch.delenv("BETA_TOKEN")
            with pytest.raises(RuntimeError, match="BETA_TOKEN is not set"):
                await bootstrap_servers(db, gateway, fresh)

            # So does a blocked address.
            servers_file.write_text(json.dumps([{"slug": "meta", "name": "Metadata", "url": "http://169.254.169.254/mcp"}]))
            with pytest.raises(HTTPException):
                await bootstrap_servers(db, gateway, fresh)
            await db.dispose()
            tg.cancel_scope.cancel()
