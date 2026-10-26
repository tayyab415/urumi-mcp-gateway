"""The public demo's sign-in list (DEMO_LOGIN), and the store addresses /api/auth/me reports."""

from __future__ import annotations

import dataclasses

import anyio
import httpx2
import pytest

from gateway.config import Settings
from gateway.main import create_app
from tests.test_gateway import CSRF, login, serving

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def settings_for(tmp_path, **changes) -> Settings:
    base = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db", secret_key="test-secret-key-0123456789",
        bootstrap_admin_email="admin@example.com", bootstrap_admin_password="admin-password-1",
        public_url="http://gateway.test", cookie_secure=False, session_hours=1, refresh_interval_seconds=0,
        default_timeout_seconds=5, allow_loopback_upstreams=True,
    )
    return dataclasses.replace(base, **changes)


async def test_demo_login_is_off_by_default(tmp_path):
    async with anyio.create_task_group() as tg:
        async with serving(tg, create_app(settings_for(tmp_path))) as (gw, _), httpx2.AsyncClient(base_url=gw, headers=CSRF) as http:
            assert (await http.get("/api/auth/demo-users")).status_code == 404
            assert (await http.post("/api/auth/demo-login", json={"user_id": 1})).status_code == 404
            assert (await http.get("/api/auth/me")).status_code == 401
            tg.cancel_scope.cancel()


async def test_demo_login_lists_active_people_and_signs_in_as_one(tmp_path):
    settings = settings_for(tmp_path, demo_login=True, store_public_url="https://shop.example",
                            store_domain="stores.example")
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, create_app(settings)) as (gw, _),
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin,
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as visitor,
        ):
            await login(admin, "admin@example.com", "admin-password-1")
            for email in ("bob@example.com", "gone@example.com"):
                invite = (await admin.post("/api/invites", json={"email": email, "role": "member"})).json()
                token = invite["invite_url"].split("token=")[1]
                async with httpx2.AsyncClient(base_url=gw, headers=CSRF) as joining:
                    r = await joining.post(f"/api/invites/accept/{token}",
                                           json={"name": email.split("@")[0].title(), "password": "member-password-1"})
                assert r.status_code == 200, r.text
            gone = next(u for u in (await admin.get("/api/users")).json() if u["email"] == "gone@example.com")
            assert (await admin.patch(f"/api/users/{gone['id']}", json={"active": False})).status_code == 200

            # Admins first; a deactivated person is not offered.
            people = (await visitor.get("/api/auth/demo-users")).json()
            assert [p["email"] for p in people] == ["admin@example.com", "bob@example.com"]
            assert "password_hash" not in people[1]

            bob = people[1]
            # Without the CSRF header, another site could sign a visitor in as someone else.
            r = await httpx2.AsyncClient(base_url=gw).post("/api/auth/demo-login", json={"user_id": bob["id"]})
            assert r.status_code == 403
            assert (await visitor.post("/api/auth/demo-login", json={"user_id": gone["id"]})).status_code == 404

            r = await visitor.post("/api/auth/demo-login", json={"user_id": bob["id"]})
            assert r.status_code == 200, r.text
            me = (await visitor.get("/api/auth/me")).json()
            assert me["email"] == "bob@example.com" and me["role"] == "member"
            assert me["store_url"] == "https://shop.example" and me["store_domain"] == "stores.example"
            # A member signed in this way is still a member.
            assert (await visitor.get("/api/users")).status_code == 403
            tg.cancel_scope.cancel()
