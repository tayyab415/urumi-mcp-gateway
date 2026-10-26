"""OAuth sign-in for MCP clients, end to end, over real HTTP.

The gateway and a small upstream run under uvicorn. The MCP SDK's own OAuth
client does discovery, registration, PKCE and refresh. A function stands in for
the browser: it opens the consent page, logs in or reuses the dashboard cookie,
and presses Allow or Deny. Lower-level tests walk the same endpoints by hand to
reach the cases a well-behaved client never sends.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl
from sqlalchemy import func, select, update

from gateway import oauth
from gateway.config import Settings
from gateway.db import Database, OAuthClient, OAuthToken as TokenRow
from gateway.main import create_app
from gateway.security import sha256_hex
from tests.test_gateway import CSRF, free_port, http_app, login, make_alpha, mcp_client, serving

pytestmark = pytest.mark.anyio

CALLBACK = "http://localhost:33333/callback"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ----------------------------------------------------------------- scaffolding


def recording(app, seen: list):
    """Wraps an upstream and keeps the Authorization header of every request it receives."""

    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            seen.append(dict(scope["headers"]).get(b"authorization"))
        await app(scope, receive, send)

    return wrapped


@dataclass
class Stack:
    gw: str
    settings: Settings
    admin: httpx2.AsyncClient  # the dashboard API, logged in as the admin; its cookie jar is also "the admin's browser"
    upstream_auth: list = field(default_factory=list)


def settings_at(tmp_path, public_url: str) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db", secret_key="test-secret-key-0123456789",
        bootstrap_admin_email="admin@example.com", bootstrap_admin_password="admin-password-1",
        public_url=public_url, cookie_secure=False, session_hours=1, refresh_interval_seconds=0,
        default_timeout_seconds=5, allow_loopback_upstreams=True,
    )


@asynccontextmanager
async def stack(tmp_path) -> AsyncIterator[Stack]:
    """The gateway at http://127.0.0.1:<port>, which is also its PUBLIC_URL, with one upstream registered."""
    port = free_port()
    settings = settings_at(tmp_path, f"http://127.0.0.1:{port}")
    seen: list = []
    async with anyio.create_task_group() as tg:
        async with (
            serving(tg, recording(http_app(make_alpha()), seen)) as (alpha_url, _),
            serving(tg, create_app(settings), port=port) as (gw, _),
            httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin,
        ):
            await login(admin, "admin@example.com", "admin-password-1")
            r = await admin.post("/api/servers", json={"slug": "alpha", "name": "Alpha", "url": f"{alpha_url}/mcp"})
            assert r.status_code == 201, r.text
            yield Stack(gw, settings, admin, seen)
        tg.cancel_scope.cancel()


async def add_member(st: Stack) -> httpx2.AsyncClient:
    """Bob, a member, logged in to the dashboard on the returned client."""
    invite = (await st.admin.post("/api/invites", json={"email": "bob@example.com", "role": "member"})).json()
    bob = httpx2.AsyncClient(base_url=st.gw, headers=CSRF)
    r = await bob.post(f"/api/invites/accept/{invite['invite_url'].split('token=')[1]}",
                       json={"name": "Bob", "password": "bob-password-1"})
    assert r.status_code == 200, r.text
    return bob


class MemoryStorage(TokenStorage):
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


class Browser:
    """What a person's browser does at the consent page."""

    def __init__(self, http: httpx2.AsyncClient) -> None:
        self.http = http
        self.page = ""

    async def open(self, authorize_url: str) -> httpx2.Response:
        page = await self.http.get(authorize_url, follow_redirects=True)
        self.page = page.text
        return page

    def form(self, decision: str, email: str | None = None, password: str | None = None) -> dict[str, str]:
        form = {name: re.search(rf'name="{name}" value="([^"]*)"', self.page).group(1) for name in ("request", "csrf", "mode")}
        form["decision"] = decision
        if email is not None:
            form.update(email=email, password=password or "")
        return form

    async def decide(self, authorize_url: str, decision: str = "allow", email: str | None = None, password: str | None = None) -> httpx2.Response:
        """Open the authorization URL, then press Allow or Deny. Returns the response with the callback redirect."""
        page = await self.open(authorize_url)
        assert page.status_code == 200, page.text
        return await self.http.post(urljoin(str(page.url), "/oauth/consent"), data=self.form(decision, email, password))


def callback_params(response: httpx2.Response) -> dict[str, str]:
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


class SignIn:
    """The SDK client's redirect and callback handlers, driving a Browser."""

    def __init__(self, browser: Browser, **credentials: str) -> None:
        self.browser, self.credentials = browser, credentials
        self.result: AuthorizationCodeResult | None = None
        self.opened = 0

    async def redirect(self, url: str) -> None:
        self.opened += 1
        q = callback_params(await self.browser.decide(url, **self.credentials))
        self.result = AuthorizationCodeResult(code=q["code"], state=q["state"], iss=q.get("iss"))

    async def callback(self) -> AuthorizationCodeResult:
        assert self.result is not None
        return self.result


def sdk_provider(gw: str, storage: MemoryStorage, sign_in: SignIn, name: str = "Test Claude") -> OAuthClientProvider:
    metadata = OAuthClientMetadata(client_name=name, redirect_uris=[AnyUrl(CALLBACK)], token_endpoint_auth_method="none",
                                   grant_types=["authorization_code", "refresh_token"], response_types=["code"])
    return OAuthClientProvider(f"{gw}/mcp", metadata, storage, sign_in.redirect, sign_in.callback)


@asynccontextmanager
async def oauth_client(gw: str, storage: MemoryStorage, sign_in: SignIn, name: str = "Test Claude") -> AsyncIterator[tuple[Client, OAuthClientProvider]]:
    provider = sdk_provider(gw, storage, sign_in, name)
    async with httpx2.AsyncClient(auth=provider, timeout=30) as http:
        async with Client(streamable_http_client(f"{gw}/mcp", http_client=http), cache=None) as client:
            yield client, provider


async def mcp_post(gw: str, token: str) -> httpx2.Response:
    async with httpx2.AsyncClient() as http:
        return await http.post(f"{gw}/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}, json=LIST_TOOLS)


# ----------------------------------------------------- by-hand protocol helpers


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


async def register(http: httpx2.AsyncClient, gw: str, redirect_uri: str = CALLBACK, name: str = "Raw app", **extra) -> httpx2.Response:
    return await http.post(f"{gw}/register", json={
        "client_name": name, "redirect_uris": [redirect_uri], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], **extra,
    })


def authorize_url(gw: str, client_id: str, challenge: str, *, redirect_uri: str = CALLBACK, resource: str | None = None,
                  state: str = "state-1", scope: str = "mcp:tools") -> str:
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256", "scope": scope, "resource": resource or f"{gw}/mcp"}
    return f"{gw}/authorize?{urlencode(params)}"


async def token_request(http: httpx2.AsyncClient, gw: str, **form) -> httpx2.Response:
    return await http.post(f"{gw}/token", data=form)


async def raw_sign_in(st: Stack, browser: Browser, *, name: str = "Raw app", **credentials: str) -> dict:
    """Registration, consent and code exchange by hand. Returns the pieces a test wants to poke at."""
    async with httpx2.AsyncClient() as http:
        client = (await register(http, st.gw, name=name)).json()
        verifier, challenge = pkce()
        q = callback_params(await browser.decide(authorize_url(st.gw, client["client_id"], challenge), **credentials))
        r = await token_request(http, st.gw, grant_type="authorization_code", code=q["code"], redirect_uri=CALLBACK,
                                client_id=client["client_id"], code_verifier=verifier, resource=f"{st.gw}/mcp")
        assert r.status_code == 200, r.text
        return {"client": client, "code": q["code"], "verifier": verifier, "tokens": r.json()}


# --------------------------------------------------------------------- tests


def test_issuer_rules():
    """The issuer is PUBLIC_URL as a plain string: https, or localhost for development, and no path."""
    cfg = oauth.OAuthConfig.from_public_url("https://App.34-67-189-86.sslip.io")
    assert cfg.issuer == "https://app.34-67-189-86.sslip.io"  # no trailing slash, which AnyHttpUrl would add
    assert cfg.resource == "https://app.34-67-189-86.sslip.io/mcp"
    assert cfg.metadata_url == "https://app.34-67-189-86.sslip.io/.well-known/oauth-protected-resource/mcp"
    for ok in ("http://localhost:8080", "http://127.0.0.1:18090", "https://gateway.example:8443"):
        assert oauth.OAuthConfig.from_public_url(ok).issuer == ok
    for bad in ("http://gateway.test", "http://10.0.0.5:8080", "https://gateway.example/base", "https://gateway.example?x=1"):
        with pytest.raises(ValueError):
            oauth.OAuthConfig.from_public_url(bad)


async def test_browser_sign_in_with_the_sdk_client(tmp_path):
    async with stack(tmp_path) as st:
        bob_dashboard = await add_member(st)
        admin_key = (await st.admin.post("/api/keys", json={"name": "cli"})).json()["key"]

        # --- the admin has a dashboard cookie, so the consent page skips the password
        admin_storage, admin_in = MemoryStorage(), SignIn(Browser(st.admin))
        async with oauth_client(st.gw, admin_storage, admin_in) as (root, _):
            assert "alpha__create_thing" in {t.name for t in (await root.list_tools()).tools}
            assert not (await root.call_tool("alpha__create_thing", {"name": "x"})).is_error
        assert admin_in.opened == 1
        page = admin_in.browser.page
        assert "Signed in as" in page and "admin@example.com" in page and 'type="password"' not in page
        assert "Test Claude" in page and "disconnect it at any time on the Connect page" in page

        # --- Bob has no cookie in this browser, so he types his password
        bob_browser = Browser(httpx2.AsyncClient())
        bob_storage, bob_in = MemoryStorage(), SignIn(bob_browser, email="bob@example.com", password="bob-password-1")
        async with oauth_client(st.gw, bob_storage, bob_in) as (bob, bob_auth):
            assert 'type="password"' in bob_browser.page and "Signed in as" not in bob_browser.page
            # His role still limits him: the write tool is not offered and not allowed.
            assert {t.name for t in (await bob.list_tools()).tools} == {"alpha__list_products"}
            assert "ball" in str((await bob.call_tool("alpha__list_products", {})).structured_content)
            assert (await bob.call_tool("alpha__create_thing", {"name": "x"})).is_error

            # --- the audit log says which app acted
            events = (await st.admin.get("/api/audit", params={"limit": 50})).json()
            bobs = [e for e in events if e["user_email"] == "bob@example.com"]
            assert bobs and all(e["via"] == "mcp" and e["api_key_prefix"] == "oauth:Test Claude" for e in bobs)
            assert {e["outcome"] for e in bobs} == {"ok", "denied"}
            assert any(e["user_email"] == "admin@example.com" and e["api_key_prefix"].startswith("oauth:") for e in events)

            # --- refresh: an expired access token is replaced, and the refresh token changes too
            before = bob_storage.tokens
            assert before.access_token.startswith("urumioat_") and before.refresh_token.startswith("urumiort_")
            bob_auth.context.token_expiry_time = time.time() - 10
            await bob.list_tools()
            after = bob_storage.tokens
            assert after.access_token != before.access_token and after.refresh_token != before.refresh_token
            assert bob_in.opened == 1  # refreshed without another trip to the browser

        # --- Connected apps: people see their own, admins can see all, nobody disconnects someone else's
        mine = (await bob_dashboard.get("/api/oauth/grants")).json()
        assert [(g["client_name"], g["user_email"]) for g in mine] == [("Test Claude", "bob@example.com")]
        assert {g["user_email"] for g in (await st.admin.get("/api/oauth/grants", params={"all": "true"})).json()} == {"admin@example.com", "bob@example.com"}
        assert [g["user_email"] for g in (await st.admin.get("/api/oauth/grants")).json()] == ["admin@example.com"]
        assert (await bob_dashboard.get("/api/oauth/grants", params={"all": "true"})).status_code == 403
        admin_grant = (await st.admin.get("/api/oauth/grants")).json()[0]
        assert (await bob_dashboard.delete(f"/api/oauth/grants/{admin_grant['id']}")).status_code == 404
        assert (await mcp_post(st.gw, admin_storage.tokens.access_token)).status_code == 200

        # --- disconnecting ends the very next request, and the refresh token with it
        assert (await bob_dashboard.delete(f"/api/oauth/grants/{mine[0]['id']}")).status_code == 200
        gone = await mcp_post(st.gw, bob_storage.tokens.access_token)
        assert gone.status_code == 401 and 'error="invalid_token"' in gone.headers["www-authenticate"]
        assert (await mcp_post(st.gw, admin_storage.tokens.access_token)).status_code == 200  # the admin's grant is separate
        async with httpx2.AsyncClient() as http:
            r = await token_request(http, st.gw, grant_type="refresh_token", refresh_token=bob_storage.tokens.refresh_token,
                                    client_id=bob_storage.client_info.client_id)
            assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        assert (await bob_dashboard.delete(f"/api/oauth/grants/{mine[0]['id']}")).status_code == 404
        assert (await bob_dashboard.get("/api/oauth/grants")).json() == []

        # --- API keys work as before, and no inbound credential ever reached the upstream
        async with mcp_client(st.gw, admin_key) as with_key:
            assert "alpha__list_products" in {t.name for t in (await with_key.list_tools()).tools}
        assert st.upstream_auth and all(h is None for h in st.upstream_auth)
        await bob_browser.http.aclose()


async def test_discovery_and_the_401_challenge(tmp_path):
    async with stack(tmp_path) as st, httpx2.AsyncClient() as http:
        prm_url = f"{st.gw}/.well-known/oauth-protected-resource/mcp"

        r = await http.post(f"{st.gw}/mcp", headers=MCP_HEADERS, json=LIST_TOOLS)
        assert r.status_code == 401
        assert f'resource_metadata="{prm_url}"' in r.headers["www-authenticate"]
        assert 'error="invalid_token"' not in r.headers["www-authenticate"]  # no credential was sent

        for bad in ("urumioat_not-a-real-token", "urumi_not-a-real-key"):
            r = await mcp_post(st.gw, bad)
            assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
            assert f'resource_metadata="{prm_url}"' in r.headers["www-authenticate"]

        # Both locations a client may try serve the same document, and it names the resource and the issuer.
        path_form = (await http.get(prm_url)).json()
        root_form = (await http.get(f"{st.gw}/.well-known/oauth-protected-resource")).json()
        assert path_form == root_form
        assert path_form["resource"] == f"{st.gw}/mcp" and path_form["authorization_servers"] == [st.gw]

        # The issuer has no trailing slash, so `iss` and the metadata compare equal as plain strings (RFC 9207).
        meta = (await http.get(f"{st.gw}/.well-known/oauth-authorization-server")).json()
        assert meta["issuer"] == st.gw
        assert meta["authorization_endpoint"] == f"{st.gw}/authorize" and meta["token_endpoint"] == f"{st.gw}/token"
        assert meta["registration_endpoint"] == f"{st.gw}/register" and meta["revocation_endpoint"] == f"{st.gw}/revoke"
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert "none" in meta["token_endpoint_auth_methods_supported"]  # Claude Code and Cursor are public clients
        assert meta["authorization_response_iss_parameter_supported"] is True
        assert "mcp:tools" in meta["scopes_supported"]

        assert (await st.admin.get("/api/auth/me")).json()["oauth_enabled"] is True


async def test_oauth_stays_off_without_https(tmp_path):
    """An http PUBLIC_URL that is not localhost cannot carry tokens safely, so the gateway keeps to API keys."""
    settings = settings_at(tmp_path, "http://gateway.test")
    async with anyio.create_task_group() as tg:
        async with serving(tg, create_app(settings)) as (gw, _), httpx2.AsyncClient(base_url=gw, headers=CSRF) as admin:
            await login(admin, "admin@example.com", "admin-password-1")
            assert (await admin.get("/api/auth/me")).json()["oauth_enabled"] is False
            assert (await admin.get("/.well-known/oauth-authorization-server")).status_code == 404
            assert (await admin.post("/register", json={})).status_code in (404, 405)
            r = await admin.post("/mcp", headers=MCP_HEADERS, json=LIST_TOOLS)
            assert r.status_code == 401 and r.headers["www-authenticate"] == 'Bearer realm="urumi-gateway"'
            key = (await admin.post("/api/keys", json={"name": "cli"})).json()["key"]
            async with mcp_client(gw, key) as client:
                assert await client.list_tools()
        tg.cancel_scope.cancel()


async def test_deny_and_pkce(tmp_path):
    async with stack(tmp_path) as st, httpx2.AsyncClient() as http:
        browser = Browser(st.admin)
        client = (await register(http, st.gw)).json()
        assert client["token_endpoint_auth_method"] == "none" and "client_secret" not in client  # public client, nothing to leak

        # --- Deny sends the browser back with access_denied, the app's state and the issuer
        _, challenge = pkce()
        q = callback_params(await browser.decide(authorize_url(st.gw, client["client_id"], challenge, state="keep-me"), "deny"))
        assert q == {"error": "access_denied", "error_description": "The user denied the request", "state": "keep-me", "iss": st.gw}
        # The request is spent: pressing the button again, or opening the page, finds nothing.
        again = await browser.http.post(f"{st.gw}/oauth/consent", data=browser.form("allow"))
        assert again.status_code == 400

        # --- a wrong PKCE verifier gets no tokens, and the right one does
        verifier, challenge = pkce()
        q = callback_params(await browser.decide(authorize_url(st.gw, client["client_id"], challenge)))
        assert q["iss"] == st.gw and q["state"] == "state-1"
        base = {"grant_type": "authorization_code", "code": q["code"], "redirect_uri": CALLBACK, "client_id": client["client_id"]}
        r = await token_request(http, st.gw, **base, code_verifier=pkce()[0])
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant" and "code_verifier" in r.json()["error_description"]
        r = await token_request(http, st.gw, **base, code_verifier=verifier)
        assert r.status_code == 200 and r.json()["token_type"] == "Bearer" and r.json()["expires_in"] == 3600
        assert (await mcp_post(st.gw, r.json()["access_token"])).status_code == 200

        # --- only S256 exists: a plain challenge, or one that is not a SHA-256 digest, is refused at /authorize
        params = urlsplit(authorize_url(st.gw, client["client_id"], challenge)).query + "&code_challenge_method=plain"
        r = await http.get(f"{st.gw}/authorize?{params}")
        assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]
        r = await http.get(authorize_url(st.gw, client["client_id"], "too-short"))
        assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]


async def test_codes_and_refresh_tokens_work_once(tmp_path):
    async with stack(tmp_path) as st, httpx2.AsyncClient() as http:
        browser = Browser(st.admin)

        # --- an authorization code used twice ends the grant that the first use created
        first = await raw_sign_in(st, browser)
        client_id = first["client"]["client_id"]
        assert (await mcp_post(st.gw, first["tokens"]["access_token"])).status_code == 200
        r = await token_request(http, st.gw, grant_type="authorization_code", code=first["code"], redirect_uri=CALLBACK,
                                client_id=client_id, code_verifier=first["verifier"])
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        assert (await mcp_post(st.gw, first["tokens"]["access_token"])).status_code == 401

        # --- a refresh token works once and is replaced
        second = await raw_sign_in(st, browser)
        client_id = second["client"]["client_id"]
        old_refresh = second["tokens"]["refresh_token"]
        r = await token_request(http, st.gw, grant_type="refresh_token", refresh_token=old_refresh, client_id=client_id, resource=f"{st.gw}/mcp")
        assert r.status_code == 200
        fresh = r.json()
        assert fresh["refresh_token"] != old_refresh and fresh["access_token"] != second["tokens"]["access_token"]
        assert (await mcp_post(st.gw, fresh["access_token"])).status_code == 200

        # --- the old one coming back means a copy exists somewhere, so the whole grant ends
        r = await token_request(http, st.gw, grant_type="refresh_token", refresh_token=old_refresh, client_id=client_id)
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        assert (await mcp_post(st.gw, fresh["access_token"])).status_code == 401
        r = await token_request(http, st.gw, grant_type="refresh_token", refresh_token=fresh["refresh_token"], client_id=client_id)
        assert r.status_code == 400

        # --- another client cannot use this client's refresh token
        third = await raw_sign_in(st, browser)
        other = (await register(http, st.gw, name="Other")).json()
        r = await token_request(http, st.gw, grant_type="refresh_token", refresh_token=third["tokens"]["refresh_token"], client_id=other["client_id"])
        assert r.status_code == 400

        # --- revoking one token (RFC 7009) ends the grant, so the other token stops too
        r = await http.post(f"{st.gw}/revoke", data={"token": third["tokens"]["access_token"], "client_id": third["client"]["client_id"]})
        assert r.status_code == 200
        assert (await mcp_post(st.gw, third["tokens"]["access_token"])).status_code == 401
        r = await token_request(http, st.gw, grant_type="refresh_token", refresh_token=third["tokens"]["refresh_token"],
                                client_id=third["client"]["client_id"])
        assert r.status_code == 400

        # --- signing in again replaces the earlier approval instead of piling up
        fourth = await raw_sign_in(st, browser, name="Same app twice")
        assert (await mcp_post(st.gw, fourth["tokens"]["access_token"])).status_code == 200
        names = [g["client_name"] for g in (await st.admin.get("/api/oauth/grants")).json()]
        assert names.count("Same app twice") == 1


async def test_tokens_are_for_this_server_only(tmp_path):
    async with stack(tmp_path) as st, httpx2.AsyncClient() as http:
        browser = Browser(st.admin)
        client = (await register(http, st.gw)).json()
        _, challenge = pkce()

        # --- asking for a token for some other resource is refused at /authorize...
        r = await http.get(authorize_url(st.gw, client["client_id"], challenge, resource="https://other.example/mcp"))
        assert r.status_code == 302
        refusal = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
        assert r.headers["location"].startswith(CALLBACK) and refusal["error"] == "invalid_target" and refusal["state"] == "state-1"

        # --- ...and at /token, before the client is even looked at
        r = await token_request(http, st.gw, grant_type="authorization_code", code="x", client_id=client["client_id"],
                                code_verifier="x" * 43, resource="https://other.example/mcp")
        assert r.status_code == 400 and r.json()["error"] == "invalid_target"
        r = await token_request(http, st.gw, grant_type="refresh_token", refresh_token="x", client_id=client["client_id"],
                                resource=f"{st.gw}/mcp&resource=https://other.example/mcp")
        assert r.status_code == 400 and r.json()["error"] == "invalid_target"

        # --- a token stored for another audience is not accepted on /mcp, whatever else is true of it
        signed = await raw_sign_in(st, browser)
        access = signed["tokens"]["access_token"]
        db = Database(st.settings.database_url)
        try:
            async def set_resource(value: str | None) -> None:
                async with db.session() as s:
                    await s.execute(update(TokenRow).where(TokenRow.token_hash == sha256_hex(access)).values(resource=value))
                    await s.commit()

            await set_resource("https://other.example/mcp")
            r = await mcp_post(st.gw, access)
            assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
            await set_resource(None)  # no audience recorded is accepted
            assert (await mcp_post(st.gw, access)).status_code == 200
            await set_resource(f"{st.gw}/mcp")
            assert (await mcp_post(st.gw, access)).status_code == 200
        finally:
            await db.dispose()


async def test_deactivating_a_person_stops_their_tokens(tmp_path):
    async with stack(tmp_path) as st:
        bob_dashboard = await add_member(st)
        browser = Browser(httpx2.AsyncClient())
        signed = await raw_sign_in(st, browser, email="bob@example.com", password="bob-password-1")
        access = signed["tokens"]["access_token"]
        assert (await mcp_post(st.gw, access)).status_code == 200
        bob_id = next(u["id"] for u in (await st.admin.get("/api/users")).json() if u["email"] == "bob@example.com")
        assert (await st.admin.patch(f"/api/users/{bob_id}", json={"active": False})).status_code == 200
        assert (await mcp_post(st.gw, access)).status_code == 401
        async with httpx2.AsyncClient() as http:
            r = await token_request(http, st.gw, grant_type="refresh_token", refresh_token=signed["tokens"]["refresh_token"],
                                    client_id=signed["client"]["client_id"])
            assert r.status_code == 400
        await browser.http.aclose()
        await bob_dashboard.aclose()


async def test_the_consent_page_is_safe_to_open_and_to_post(tmp_path):
    async with stack(tmp_path) as st, httpx2.AsyncClient() as http:
        await add_member(st)
        evil_name = "<script>alert(1)</script>‮\nClaude"
        client = (await register(http, st.gw, name=evil_name)).json()
        _, challenge = pkce()
        url = authorize_url(st.gw, client["client_id"], challenge)

        # --- the app's own name is escaped, and the page cannot be framed or run scripts
        stranger = Browser(httpx2.AsyncClient())
        page = await stranger.open(url)
        assert page.status_code == 200
        assert "<script>" not in page.text and "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
        assert "‮" not in page.text and "\n" not in page.text.split("<h1>")[1].split("</h1>")[0]
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"] and "script" not in page.headers["content-security-policy"].replace("<", "")
        assert page.headers["x-frame-options"] == "DENY" and page.headers["cache-control"] == "no-store"
        assert "localhost:33333" in page.text  # where the browser will go next

        # --- the form is refused without its token, with a token from another page, or without the cookie
        form = stranger.form("allow", "bob@example.com", "bob-password-1")
        post = lambda client_, data: client_.post(f"{st.gw}/oauth/consent", data=data)  # noqa: E731
        assert (await post(stranger.http, {k: v for k, v in form.items() if k != "csrf"})).status_code == 403
        assert (await post(stranger.http, {**form, "csrf": "guess"})).status_code == 403
        async with httpx2.AsyncClient() as attacker:  # a page on another site: has the token it scraped, not the cookie
            assert (await post(attacker, form)).status_code == 403

        # --- a wrong password stays on the page and issues nothing; the right one goes through
        wrong = await stranger.http.post(f"{st.gw}/oauth/consent", data={**form, "password": "nope"})
        assert wrong.status_code == 401 and "Wrong email or password" in wrong.text and "location" not in wrong.headers
        stale = await post(stranger.http, form)  # the form shown before the failed attempt carries an old token
        assert stale.status_code == 403
        stranger.page = wrong.text
        ok = await stranger.http.post(f"{st.gw}/oauth/consent", data=stranger.form("allow", "bob@example.com", "bob-password-1"))
        assert "code" in callback_params(ok)

        # --- a made-up or expired request id
        assert (await http.get(f"{st.gw}/oauth/consent?request=nope")).status_code == 400
        await stranger.http.aclose()


async def test_registration_and_redirect_uri_rules(tmp_path, monkeypatch):
    async with stack(tmp_path) as st, httpx2.AsyncClient() as http:
        # --- only https and loopback http redirects (and the Cursor app scheme) can be registered
        for bad in ("http://evil.example/cb", "javascript:alert(1)", "file:///etc/passwd", "https://app.example/cb#frag", "ftp://x.example/cb"):
            r = await register(http, st.gw, redirect_uri=bad)
            assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri", bad
        for good in ("https://claude.ai/api/mcp/auth_callback", "http://127.0.0.1:8787/callback", "http://[::1]:9/cb", "cursor://anysphere.cursor/oauth/cb"):
            assert (await register(http, st.gw, redirect_uri=good)).status_code == 201, good
        too_many = await http.post(f"{st.gw}/register", json={"redirect_uris": [f"https://a.example/{i}" for i in range(20)]})
        assert too_many.status_code == 400
        huge = await register(http, st.gw, software_id="x" * 20000)
        assert huge.status_code == 400 and huge.json()["error"] == "invalid_client_metadata"
        # A client that asks for a secret gets none: every client is public.
        r = await http.post(f"{st.gw}/register", json={"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "client_secret_post"})
        assert r.status_code == 201 and r.json()["token_endpoint_auth_method"] == "none" and "client_secret" not in r.json()

        # --- a loopback redirect may change its port between sign-ins (RFC 8252 7.3), nothing else about it
        client = (await register(http, st.gw, redirect_uri="http://localhost:1111/callback")).json()
        _, challenge = pkce()
        for uri, ok in (("http://localhost:2222/callback", True), ("http://localhost:1111/callback", True),
                        ("http://localhost:2222/other", False), ("http://127.0.0.1:2222/callback", False),
                        ("https://localhost:2222/callback", False), ("http://evil.example:1111/callback", False)):
            r = await http.get(authorize_url(st.gw, client["client_id"], challenge, redirect_uri=uri))
            assert (r.status_code == 302 and "/oauth/consent?request=" in r.headers["location"]) is ok, uri
        # --- only the two known scopes exist. A client may ask for offline_access without having registered it.
        for scope, ok in (("mcp:tools offline_access", True), ("mcp:tools admin", False)):
            r = await http.get(authorize_url(st.gw, client["client_id"], challenge, redirect_uri="http://localhost:1111/callback", scope=scope))
            assert r.status_code == 302 and ("/oauth/consent?request=" in r.headers["location"]) is ok, scope
            assert ok or "error=invalid_scope" in r.headers["location"]

        # ...and the port the app used to ask is the port its code goes to.
        verifier, challenge = pkce()
        other_port = "http://localhost:4444/callback"
        q = callback_params(await Browser(st.admin).decide(authorize_url(st.gw, client["client_id"], challenge, redirect_uri=other_port)))
        r = await token_request(http, st.gw, grant_type="authorization_code", code=q["code"], redirect_uri="http://localhost:1111/callback",
                                client_id=client["client_id"], code_verifier=verifier)
        assert r.status_code == 400  # the redirect_uri must match the one used at /authorize
        r = await token_request(http, st.gw, grant_type="authorization_code", code=q["code"], redirect_uri=other_port,
                                client_id=client["client_id"], code_verifier=verifier)
        assert r.status_code == 200

        # --- registration is open to anyone, so the pile of never-approved clients is capped; approved ones stay
        monkeypatch.setattr(oauth, "MAX_UNAPPROVED_CLIENTS", 4)
        ids = [(await register(http, st.gw, name=f"Drive-by {i}")).json()["client_id"] for i in range(8)]
        db = Database(st.settings.database_url)
        try:
            async with db.session() as s:
                left = set(await s.scalars(select(OAuthClient.client_id)))
                unapproved = await s.scalar(select(func.count()).select_from(OAuthClient).where(OAuthClient.approved_at.is_(None)))
        finally:
            await db.dispose()
        assert unapproved == 4 and client["client_id"] in left  # the approved client survived
        assert ids[-1] in left and ids[0] not in left  # the oldest unapproved ones went first
        r = await http.get(authorize_url(st.gw, ids[0], challenge))
        assert r.status_code == 400
