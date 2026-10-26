"""The /mcp rate limit counts per verified credential, not per spelling of the header."""

from __future__ import annotations

import dataclasses

import anyio
import httpx2
import pytest

from gateway.config import Settings
from gateway.main import create_app
from gateway.mcp_endpoint import RATE_LIMITED
from gateway.ratelimit import RateLimiter, parse_period
from tests.test_gateway import CSRF, login, serving

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_bucket_allows_a_burst_then_refills():
    now = [0.0]
    limiter = RateLimiter(average=10, period_seconds=1, burst=3, clock=lambda: now[0])
    assert [limiter.wait("key-a") for _ in range(3)] == [0, 0, 0]
    assert limiter.wait("key-a") == pytest.approx(0.1)
    assert limiter.wait("key-b") == 0  # another credential has its own allowance
    now[0] += 0.1
    assert limiter.wait("key-a") == 0 and limiter.wait("key-a") > 0


def test_periods_read_like_the_traefik_values():
    assert parse_period("1s") == 1 and parse_period("1m") == 60 and parse_period("500ms") == 0.5
    with pytest.raises(ValueError):
        parse_period("every second")


async def test_header_spelling_does_not_buy_a_fresh_allowance(tmp_path):
    settings = dataclasses.replace(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db", secret_key="test-secret-key-0123456789",
            bootstrap_admin_email="admin@example.com", bootstrap_admin_password="admin-password-1",
            public_url="http://gateway.test", cookie_secure=False, session_hours=1, refresh_interval_seconds=0,
            default_timeout_seconds=5, allow_loopback_upstreams=True,
        ),
        mcp_rate_average=1, mcp_rate_period="1m", mcp_rate_burst=3,
    )
    async with anyio.create_task_group() as tg:
        async with serving(tg, create_app(settings)) as (gw, _), httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin:
            await login(admin, "admin@example.com", "admin-password-1")
            key = (await admin.post("/api/keys", json={"name": "one"})).json()["key"]
            other = (await admin.post("/api/keys", json={"name": "two"})).json()["key"]

            async def tools_list(authorization: str) -> httpx2.Response:
                return await httpx2.AsyncClient().post(
                    f"{gw}/mcp", headers={"Authorization": authorization, "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                )

            # The same key written four ways: one allowance of three between them.
            spellings = [f"Bearer {key}", f"Bearer   {key}", f"bearer {key}", f"Bearer {' ' * 40}{key}"]
            statuses = [(await tools_list(a)).status_code for a in spellings]
            assert statuses == [200, 200, 200, 429]
            limited = await tools_list(f"Bearer {key}")
            assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1
            assert limited.json()["error"]["code"] == RATE_LIMITED

            # Another key is counted on its own, and a request without a valid key is a 401 first.
            assert (await tools_list(f"Bearer {other}")).status_code == 200
            assert (await tools_list("Bearer urumi_not-a-key")).status_code == 401
        tg.cancel_scope.cancel()
