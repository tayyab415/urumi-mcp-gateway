"""The audit row exists before the upstream sees a call, so a gateway crash cannot lose it."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import mcp_types as types
import pytest
from sqlalchemy import select, text

from gateway.core import Gateway, Identity
from gateway.db import AuditEvent, Database, Server, Tool
from gateway.security import CredentialBox

pytestmark = pytest.mark.anyio
SECRET = "test-secret-key-0123456789"
ADMIN = Identity(user_id=1, email="admin@example.com", role="admin", via="mcp", api_key_prefix="urumi_abc123")

# Runs the real call path in its own process, against a fake upstream that makes
# its change and then hangs, so the test can kill the process mid-call.
WORKER = """
import sys
from unittest.mock import patch

import anyio

from gateway.core import Gateway, Identity
from gateway.db import Database
from gateway.security import CredentialBox


async def main(url, marker):
    gateway = Gateway(Database(url), CredentialBox(sys.argv[3]))

    async def change_then_hang(*args):
        open(marker, "w").write("order created")
        print("CHANGED", flush=True)
        await anyio.sleep(60)

    with patch("gateway.upstreams.call_tool", change_then_hang):
        await gateway.call_tool(Identity(1, "admin@example.com", "admin", "mcp"), "fixture__create_order", {"review": 1})


anyio.run(main, sys.argv[1], sys.argv[2])
"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def fixture_db(path: Path) -> Database:
    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.create_all()
    async with db.session() as s:
        s.add(Server(slug="fixture", name="Fixture", url="http://fixture.invalid/mcp", tools=[
            Tool(name="create_order", input_schema={"type": "object"}, allowed_roles=["admin"], annotations={"readOnlyHint": False}),
        ]))
        await s.commit()
    return db


def rows(path: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(path) as con:
        return con.execute("select public_name, outcome from audit_events").fetchall()


async def test_a_gateway_killed_mid_call_leaves_a_row_that_the_next_start_marks_interrupted(tmp_path):
    path, marker = tmp_path / "gw.db", tmp_path / "upstream-change.txt"
    db = await fixture_db(path)
    worker = subprocess.Popen(
        [sys.executable, "-c", WORKER, f"sqlite+aiosqlite:///{path}", str(marker), SECRET],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    try:
        assert worker.stdout.readline().strip() == "CHANGED"
        # The upstream has made its change and the gateway has not answered yet.
        assert rows(path) == [("fixture__create_order", "pending")]
    finally:
        worker.kill()
        worker.wait(timeout=10)
    assert marker.exists() and rows(path) == [("fixture__create_order", "pending")]

    # The next gateway process to start says the outcome is unknown, rather than leaving it pending.
    assert await Gateway(db, CredentialBox(SECRET)).mark_interrupted() == 1
    async with db.session() as s:
        event = await s.scalar(select(AuditEvent))
    assert event.outcome == "interrupted" and "may have done it" in event.error
    assert event.user_email == "admin@example.com" and event.arguments == '{"review": 1}'
    await db.dispose()


async def test_a_call_whose_row_cannot_be_written_is_not_sent(tmp_path):
    db = await fixture_db(tmp_path / "gw.db")
    gateway = Gateway(db, CredentialBox(SECRET))
    sent: list[str] = []

    async def upstream(target, name, arguments):
        sent.append(name)
        return types.CallToolResult(content=[types.TextContent(type="text", text="created")])

    with patch("gateway.upstreams.call_tool", upstream):
        ok = await gateway.call_tool(ADMIN, "fixture__create_order", {})
        assert not ok.is_error and sent == ["create_order"]

        # The audit table stops accepting rows, for example a full disk.
        async with db.engine.begin() as conn:
            await conn.execute(text("CREATE TRIGGER refuse BEFORE INSERT ON audit_events BEGIN SELECT RAISE(ABORT, 'disk full'); END"))
        refused = await gateway.call_tool(ADMIN, "fixture__create_order", {})
    assert refused.is_error and "did not send it" in refused.content[0].text
    assert sent == ["create_order"]  # the second call never reached the upstream
    async with db.session() as s:
        assert [(e.outcome, e.error) for e in await s.scalars(select(AuditEvent))] == [("ok", None)]
    await db.dispose()
