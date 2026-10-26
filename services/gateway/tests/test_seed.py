"""The team seeder: people, keys and servers created through the public API, safely repeatable."""

from __future__ import annotations

import json
import stat

import anyio
import httpx2
import pytest

from gateway import seed
from gateway.config import Settings
from gateway.main import create_app

from .test_gateway import CSRF, http_app, login, make_alpha, make_beta, mcp_client, serving

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def settings_for(tmp_path) -> Settings:
    return Settings(
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


def team_config(alpha_url: str, beta_url: str) -> dict:
    return {
        "users": [
            {"email": "Ana@Example.com", "name": "Ana", "role": "admin"},
            {"email": "bob@example.com", "name": "Bob", "role": "member"},
            {"email": "chen@example.com", "name": "Chen", "role": "member"},
        ],
        "servers": [
            {"slug": "alpha", "name": "Alpha", "url": "${ALPHA_URL}/mcp", "timeout_seconds": 5},
            {"slug": "beta", "name": "Beta", "url": f"{beta_url}/mcp", "auth_type": "bearer",
             "credential_env": "BETA_TOKEN", "timeout_seconds": 5},
        ],
    }


async def test_seed_creates_the_team_and_is_safe_to_repeat(tmp_path):
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, http_app(make_alpha())) as (alpha_url, _),
            serving(tg, http_app(make_beta(), bearer="beta-token")) as (beta_url, _),
            serving(tg, create_app(settings_for(tmp_path))) as (gw, _),
        ):
            def new_client():
                return httpx2.AsyncClient(base_url=gw, headers=CSRF, timeout=30)

            config = team_config(alpha_url, beta_url)
            env = {"ALPHA_URL": alpha_url, "BETA_TOKEN": "beta-token"}

            first = await seed.seed(config, new_client, "admin@example.com", "admin-password-1", env, make_keys=True)
            assert set(first["credentials"]) == {"ana@example.com", "bob@example.com", "chen@example.com"}
            assert all(len(c["password"]) >= 10 and c["api_key"].startswith("urumi_") for c in first["credentials"].values())
            assert len({c["password"] for c in first["credentials"].values()}) == 3  # random per person
            assert any("beta" in line and "registered, status ok" in line for line in first["report"])

            # Repeating changes nothing and creates no new secrets.
            again = await seed.seed(config, new_client, "admin@example.com", "admin-password-1", env, make_keys=True)
            assert again["credentials"] == {}
            assert all("already" in line for line in again["report"])

            # The saved passwords work, and roles came through.
            ana, bob = first["credentials"]["ana@example.com"], first["credentials"]["bob@example.com"]
            async with new_client() as http:
                await login(http, "bob@example.com", bob["password"])
                assert (await http.get("/api/users")).status_code == 403
            async with new_client() as http:
                await login(http, "ana@example.com", ana["password"])
                assert (await http.get("/api/users")).status_code == 200

            # The saved keys work on /mcp, and the role decides what each person sees.
            async with mcp_client(gw, bob["api_key"]) as bob_mcp, mcp_client(gw, ana["api_key"]) as ana_mcp:
                assert "alpha__create_thing" not in {t.name for t in (await bob_mcp.list_tools()).tools}
                assert "alpha__create_thing" in {t.name for t in (await ana_mcp.list_tools()).tools}


async def test_seed_refuses_a_server_whose_credential_is_missing(tmp_path):
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, http_app(make_beta(), bearer="beta-token")) as (beta_url, _),
            serving(tg, create_app(settings_for(tmp_path))) as (gw, _),
        ):
            config = {"servers": [{"slug": "beta", "name": "Beta", "url": f"{beta_url}/mcp", "auth_type": "bearer", "credential_env": "BETA_TOKEN"}]}
            with pytest.raises(seed.SeedError, match="BETA_TOKEN"):
                await seed.seed(config, lambda: httpx2.AsyncClient(base_url=gw, headers=CSRF, timeout=30),
                                "admin@example.com", "admin-password-1", {})


def test_credentials_file_is_private_and_merges(tmp_path):
    path = tmp_path / "sub" / "creds.json"
    seed.write_credentials(path, {"a@x.test": {"password": "one"}})
    seed.write_credentials(path, {"b@x.test": {"password": "two"}})
    assert set(json.loads(path.read_text())) == {"a@x.test", "b@x.test"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_new_people_need_somewhere_to_save_their_passwords(tmp_path, monkeypatch, capsys):
    config = tmp_path / "team.json"
    config.write_text(json.dumps({"users": [{"email": "a@x.test", "name": "A"}]}))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "admin-password-1")
    assert seed.main(["--url", "http://127.0.0.1:1", "--config", str(config)]) == 1
    assert "--credentials-out is required" in capsys.readouterr().err


def test_env_references():
    assert seed.expand("${A}/mcp", {"A": "http://x"}) == "http://x/mcp"
    assert seed.expand("${A:-fallback}", {}) == "fallback"
    with pytest.raises(seed.SeedError):
        seed.expand("${A}", {})
