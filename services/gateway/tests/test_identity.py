"""Forwarding the caller's name to an upstream: off by default, a display name only, and upgrade-safe."""

from __future__ import annotations

import json
from urllib.parse import unquote

import anyio
import httpx2
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp_types import ToolAnnotations
from sqlalchemy import text

from gateway.db import Database
from gateway.main import create_app
from gateway import seed

from .test_gateway import CSRF, http_app, login, mcp_client, serving
from .test_seed import settings_for

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_echo() -> MCPServer:
    echo = MCPServer("echo")

    @echo.tool(annotations=ToolAnnotations(read_only_hint=True))
    def whoami(ctx: Context) -> str:
        """The headers this server received, minus the secret."""
        return json.dumps({k: v for k, v in (ctx.headers or {}).items() if k.startswith("x-urumi")})

    return echo


async def test_name_is_sent_only_when_the_admin_turns_it_on(tmp_path):
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, http_app(make_echo())) as (echo_url, _),
            serving(tg, create_app(settings_for(tmp_path))) as (gw, _),
        ):
            def new_client():
                return httpx2.AsyncClient(base_url=gw, headers=CSRF, timeout=30)

            config = {
                "users": [{"email": "zoe@example.com", "name": "Zoë Ångström", "role": "member"}],
                "servers": [{"slug": "echo", "name": "Echo", "url": f"{echo_url}/mcp"}],
            }
            made = await seed.seed(config, new_client, "admin@example.com", "admin-password-1", {}, make_keys=True)
            zoe_key = made["credentials"]["zoe@example.com"]["api_key"]

            async def ask() -> dict:
                async with mcp_client(gw, zoe_key) as client:
                    result = await client.call_tool("echo__whoami", {})
                return json.loads(result.content[0].text)

            assert await ask() == {}  # the default: nothing about the caller leaves the gateway

            async with new_client() as admin:
                await login(admin, "admin@example.com", "admin-password-1")
                (server,) = [s for s in (await admin.get("/api/servers")).json() if s["slug"] == "echo"]
                assert server["forward_identity"] is False
                patched = await admin.patch(f"/api/servers/{server['id']}", json={"forward_identity": True})
                assert patched.status_code == 200 and patched.json()["forward_identity"] is True

            sent = await ask()
            assert unquote(sent["x-urumi-user"]) == "Zoë Ångström"  # percent-encoded on the wire, so any name is a valid header
            assert "zoe@example.com" not in json.dumps(sent)  # the display name, never the email

            async with new_client() as admin:
                await login(admin, "admin@example.com", "admin-password-1")
                await admin.patch(f"/api/servers/{server['id']}", json={"forward_identity": False})
            assert await ask() == {}


async def test_an_older_database_gets_the_new_column(tmp_path):
    path = tmp_path / "old.db"
    db = Database(f"sqlite+aiosqlite:///{path}")
    async with db.engine.begin() as conn:
        # The servers table as it was before the setting existed.
        await conn.execute(text(
            "CREATE TABLE servers (id INTEGER PRIMARY KEY, slug VARCHAR(32) UNIQUE, name VARCHAR(100), url VARCHAR(500), enabled BOOLEAN,"
            " auth_type VARCHAR(16), auth_header VARCHAR(100), credential_enc TEXT, timeout_seconds FLOAT, status VARCHAR(16),"
            " last_error TEXT, last_checked_at DATETIME, last_ok_at DATETIME, latency_ms INTEGER, protocol_version VARCHAR(32),"
            " created_at DATETIME, updated_at DATETIME)"
        ))
        await conn.execute(text("INSERT INTO servers (id, slug, name, url, enabled, auth_type, timeout_seconds, status) VALUES (1, 'old', 'Old', 'http://x/mcp', 1, 'none', 10, 'ok')"))
    await db.create_all()
    await db.create_all()  # running it again is harmless
    async with db.engine.connect() as conn:
        row = (await conn.execute(text("SELECT slug, forward_identity FROM servers"))).one()
    assert tuple(row) == ("old", 0)
    await db.dispose()
