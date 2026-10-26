"""REST API for the React dashboard.

Authentication is a login session in an HttpOnly cookie. API keys are not
accepted here, and session cookies are not accepted on /mcp, so the two doors
stay separate.

Every state-changing request must carry the `X-Urumi-CSRF` header. A browser
only sends a custom header cross-site after a CORS preflight, which this API
never approves, so another site cannot make a logged-in admin's browser act.

Authorization is checked here, on the server. The dashboard hides admin screens
from members for convenience only.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import anyio
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from gateway import oauth, upstreams
from gateway.config import Settings
from gateway.core import ROLES, SEPARATOR, Gateway, Identity, definition_of, has_hidden_characters, pending_review, public_name
from gateway.db import ApiKey, AuditEvent, Database, Invite, LoginSession, OAuthClient, OAuthGrant, Server, Tool, User, utcnow
from gateway.security import CredentialBox, hash_password, new_api_key, new_token, sha256_hex, verify_password

COOKIE = "urumi_session"
Role = Literal["admin", "member"]
SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
BLOCKED_HOSTS = {"metadata.google.internal", "metadata", "169.254.169.254"}
# Compared against when the email is unknown, so both paths cost one scrypt.
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


def iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


# --------------------------------------------------------------------- deps


async def session_user(db: Database, token: str) -> User | None:
    """The active user behind a dashboard session cookie, or None."""
    async with db.session() as s:
        session = await s.scalar(select(LoginSession).where(LoginSession.token_hash == sha256_hex(token)))
        if session is None or session.expires_at < utcnow() or not session.user.active:
            return None
        return session.user


async def check_password(db: Database, email: str, password: str) -> User | None:
    """The user for this email and password, or None. One rule for the login form and the OAuth consent page."""
    async with db.session() as s:
        user = await s.scalar(select(User).where(User.email == email.strip().lower()))
    # Same work either way, so the answer does not reveal which emails exist.
    valid = verify_password(password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    return user if user is not None and valid and user.active else None


async def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not logged in")
    user = await session_user(request.app.state.db, token)
    if user is None:
        raise HTTPException(401, "Session expired")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-urumi-csrf") != "1":
        raise HTTPException(403, "Missing CSRF header")
    return user


async def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admins only")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(admin_user)]


def chat_identity(user: User, chat_id: str | None = None) -> Identity:
    return Identity(user_id=user.id, email=user.email, role=user.role, via="chat", chat_id=chat_id, name=user.name)  # type: ignore[arg-type]


# ------------------------------------------------------------------ schemas


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: Role
    active: bool
    created_at: str | None = None
    active_keys: int | None = None


def user_out(u: User, active_keys: int | None = None) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role, active=u.active,  # type: ignore[arg-type]
                   created_at=iso(u.created_at), active_keys=active_keys)


class LoginIn(BaseModel):
    email: str
    password: str


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    role: Role = "member"


class AcceptIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=10, max_length=200)


class UserPatch(BaseModel):
    role: Role | None = None
    active: bool | None = None


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def validate_slug(v: str) -> str:
    if not SLUG_RE.match(v) or SEPARATOR in v:
        raise ValueError("lowercase letters, digits and single hyphens, starting with a letter")
    # gateway__* names the gateway's own search-mode tools; store-* names provisioned stores.
    if v == "gateway" or v.startswith("store-"):
        raise ValueError("'gateway' and names starting with 'store-' are reserved")
    return v


class ServerIn(BaseModel):
    slug: str = Field(min_length=2, max_length=24, description="Tool-name prefix, e.g. woo")
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=500)
    auth_type: Literal["none", "bearer", "header"] = "none"
    auth_header: str | None = Field(default=None, max_length=100)
    credential: str | None = Field(default=None, max_length=4000)
    timeout_seconds: float = Field(default=10, ge=1, le=120)
    forward_identity: bool = Field(default=False, description="Send the caller's display name to this server (never the email)")
    enabled: bool = True

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        return validate_slug(v)


class ServerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=8, max_length=500)
    auth_type: Literal["none", "bearer", "header"] | None = None
    auth_header: str | None = Field(default=None, max_length=100)
    credential: str | None = Field(default=None, max_length=4000, description="Replaces the stored credential")
    clear_credential: bool = False
    timeout_seconds: float | None = Field(default=None, ge=1, le=120)
    forward_identity: bool | None = None
    enabled: bool | None = None


class ToolRolesIn(BaseModel):
    allowed_roles: list[Role]


class ServerTestIn(BaseModel):
    """What the add/edit form holds. Nothing here is saved."""

    slug: str = Field(min_length=2, max_length=24)
    url: str = Field(min_length=8, max_length=500)
    auth_type: Literal["none", "bearer", "header"] = "none"
    auth_header: str | None = Field(default=None, max_length=100)
    credential: str | None = Field(default=None, max_length=4000)
    timeout_seconds: float = Field(default=10, ge=1, le=120)
    # When editing, the admin may leave the credential blank to keep the stored one.
    server_id: int | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        return validate_slug(v)


# ----------------------------------------------------------- serialization


def server_out(s: Server, tool_count: int) -> dict[str, Any]:
    return {
        "id": s.id,
        "slug": s.slug,
        "name": s.name,
        "url": s.url,
        "enabled": s.enabled,
        "auth_type": s.auth_type,
        "auth_header": s.auth_header,
        # Never the value. The browser only learns whether one is stored.
        "has_credential": bool(s.credential_enc),
        "timeout_seconds": s.timeout_seconds,
        "forward_identity": s.forward_identity,
        "managed_by": s.managed_by,
        "owner_id": s.owner_id,
        "status": "disabled" if not s.enabled else s.status,
        "health": s.status,
        "last_error": s.last_error,
        "last_checked_at": iso(s.last_checked_at),
        "last_ok_at": iso(s.last_ok_at),
        "latency_ms": s.latency_ms,
        "protocol_version": s.protocol_version,
        "tool_count": tool_count,
    }


def tool_out(server: Server, t: Tool) -> dict[str, Any]:
    return {
        "name": t.name,
        "public_name": public_name(server.slug, t.name),
        "title": t.title,
        "description": t.description,
        "annotations": t.annotations or {},
        "allowed_roles": t.allowed_roles,
        "present": t.present,
        "input_schema": t.input_schema,
        # Tool pinning: held back until an admin approves the current definition.
        "pending_review": pending_review(t),
        "hidden_characters": has_hidden_characters(t.description) or has_hidden_characters(t.title),
        "approved_definition": t.approved_definition,
        "definition": definition_of(t),
    }


# ------------------------------------------------------------- validation


async def check_upstream_url(url: str, allow_loopback: bool) -> None:
    """Admins type upstream URLs, so the gateway must not become a proxy to anything.

    Cluster-internal addresses are allowed on purpose (the WooCommerce MCP server
    lives on one). Cloud metadata, link-local and, outside local development,
    loopback are refused. This runs at save time; NetworkPolicy egress rules are
    the second layer, since DNS answers can change later.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(422, "URL must be http(s)://host/...")
    if parts.username or parts.password:
        raise HTTPException(422, "Put credentials in the credential field, not the URL")
    host = parts.hostname.lower()
    if host in BLOCKED_HOSTS:
        raise HTTPException(422, "That address is blocked")
    try:
        infos = await anyio.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError:
        return  # not resolvable yet (e.g. a Service about to be created); discovery will report it
    for info in infos:
        if reason := upstreams.blocked_address(info[4][0], allow_loopback):
            raise HTTPException(422, f"{host} resolves to a blocked address ({reason})")


def credential_rules(auth_type: str, auth_header: str | None) -> None:
    if auth_type == "header" and not (auth_header and re.fullmatch(r"[A-Za-z0-9-]{1,100}", auth_header)):
        raise HTTPException(422, "Header auth needs a header name such as X-Api-Key")


def new_server(body: ServerIn, box: CredentialBox) -> Server:
    """The row for a new server. The credential is sealed before it is stored."""
    return Server(
        slug=body.slug, name=body.name, url=body.url, enabled=body.enabled,
        auth_type=body.auth_type, auth_header=body.auth_header if body.auth_type == "header" else None,
        credential_enc=box.seal(body.credential) if body.credential and body.auth_type != "none" else None,
        timeout_seconds=body.timeout_seconds, forward_identity=body.forward_identity,
    )


# ----------------------------------------------------------------- routes


def build_router(settings: Settings) -> APIRouter:
    r = APIRouter(prefix="/api")

    # -- auth ---------------------------------------------------------------

    async def start_session(db: Database, user: User, response: Response) -> None:
        token, token_hash = new_token()
        async with db.session() as s:
            s.add(LoginSession(user_id=user.id, token_hash=token_hash,
                               expires_at=utcnow() + timedelta(hours=settings.session_hours)))
            await s.commit()
        response.set_cookie(COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="strict",
                            max_age=settings.session_hours * 3600, path="/")

    @r.post("/auth/login")
    async def login(body: LoginIn, request: Request, response: Response) -> UserOut:
        db: Database = request.app.state.db
        user = await check_password(db, body.email, body.password)
        if user is None:
            raise HTTPException(401, "Wrong email or password")
        await start_session(db, user, response)
        return user_out(user)

    @r.post("/auth/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        token = request.cookies.get(COOKIE)
        if token:
            async with request.app.state.db.session() as s:
                session = await s.scalar(select(LoginSession).where(LoginSession.token_hash == sha256_hex(token)))
                if session:
                    await s.delete(session)
                    await s.commit()
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @r.post("/auth/chat-token")
    async def chat_token(user: CurrentUser, request: Request) -> dict[str, str]:
        """The credential the dashboard sends to the chat service, which forwards it to /mcp."""
        token = request.cookies.get(COOKIE, "")
        async with request.app.state.db.session() as s:
            session = await s.scalar(select(LoginSession).where(LoginSession.token_hash == sha256_hex(token)))
            if session is None:
                raise HTTPException(401, "Session expired")
            return {"token": request.app.state.chat_tokens.issue(session.id, session.token_hash), "chat_url": "/api/chat"}

    @r.get("/auth/me")
    async def me(user: CurrentUser, request: Request) -> dict[str, Any]:
        return {**user_out(user).model_dump(), "mcp_url": f"{settings.public_url}/mcp",
                "oauth_enabled": request.app.state.oauth is not None}

    # -- invitations ----------------------------------------------------------

    @r.get("/invites")
    async def list_invites(_: AdminUser, request: Request) -> list[dict[str, Any]]:
        async with request.app.state.db.session() as s:
            rows = await s.scalars(
                select(Invite).where(Invite.accepted_at.is_(None), Invite.revoked_at.is_(None),
                                     Invite.expires_at > utcnow()).order_by(Invite.created_at.desc())
            )
            return [{"id": i.id, "email": i.email, "role": i.role, "expires_at": iso(i.expires_at)} for i in rows]

    @r.post("/invites")
    async def create_invite(body: InviteIn, admin: AdminUser, request: Request) -> dict[str, Any]:
        email = body.email.strip().lower()
        token, token_hash = new_token()
        expires = utcnow() + timedelta(days=7)
        async with request.app.state.db.session() as s:
            if await s.scalar(select(User).where(User.email == email)):
                raise HTTPException(409, "That person already has an account")
            invite = Invite(email=email, role=body.role, token_hash=token_hash, created_by=admin.id, expires_at=expires)
            s.add(invite)
            await s.commit()
        # No email server in this deployment: the admin copies the link and sends it.
        return {"id": invite.id, "email": email, "role": body.role, "expires_at": iso(expires),
                "invite_url": f"{settings.public_url}/accept-invite?token={token}"}

    @r.delete("/invites/{invite_id}")
    async def revoke_invite(invite_id: int, _: AdminUser, request: Request) -> dict[str, bool]:
        async with request.app.state.db.session() as s:
            invite = await s.get(Invite, invite_id)
            if invite is None:
                raise HTTPException(404, "No such invite")
            invite.revoked_at = utcnow()
            await s.commit()
        return {"ok": True}

    async def usable_invite(s: Any, token: str) -> Invite:
        invite = await s.scalar(select(Invite).where(Invite.token_hash == sha256_hex(token)))
        if invite is None or invite.accepted_at or invite.revoked_at or invite.expires_at < utcnow():
            raise HTTPException(404, "This invitation is invalid or has expired")
        return invite

    @r.get("/invites/lookup/{token}")
    async def lookup_invite(token: str, request: Request) -> dict[str, str]:
        async with request.app.state.db.session() as s:
            invite = await usable_invite(s, token)
            return {"email": invite.email, "role": invite.role}

    @r.post("/invites/accept/{token}")
    async def accept_invite(token: str, body: AcceptIn, request: Request, response: Response) -> UserOut:
        db: Database = request.app.state.db
        async with db.session() as s:
            invite = await usable_invite(s, token)
            if await s.scalar(select(User).where(User.email == invite.email)):
                raise HTTPException(409, "That person already has an account")
            user = User(email=invite.email, name=body.name.strip(), role=invite.role,
                        password_hash=hash_password(body.password))
            s.add(user)
            invite.accepted_at = utcnow()
            await s.commit()
        await start_session(db, user, response)
        return user_out(user)

    # -- users ----------------------------------------------------------------

    @r.get("/users")
    async def list_users(_: AdminUser, request: Request) -> list[UserOut]:
        async with request.app.state.db.session() as s:
            rows = await s.execute(
                select(ApiKey.user_id, func.count()).where(ApiKey.revoked_at.is_(None)).group_by(ApiKey.user_id)
            )
            counts = {user_id: n for user_id, n in rows.all()}
            users = await s.scalars(select(User).order_by(User.created_at))
            return [user_out(u, counts.get(u.id, 0)) for u in users]

    @r.patch("/users/{user_id}")
    async def update_user(user_id: int, body: UserPatch, admin: AdminUser, request: Request) -> UserOut:
        async with request.app.state.db.session() as s:
            user = await s.get(User, user_id)
            if user is None:
                raise HTTPException(404, "No such user")
            losing_admin = user.role == "admin" and user.active and (
                body.role == "member" or body.active is False
            )
            if losing_admin:
                admins = await s.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active.is_(True)))
                if admins <= 1:
                    raise HTTPException(409, "Keep at least one active admin")
            if body.role is not None:
                user.role = body.role
            if body.active is not None:
                user.active = body.active
                if not body.active:
                    # Logged-in browsers go now; API keys stop working because the user is inactive.
                    for session in await s.scalars(select(LoginSession).where(LoginSession.user_id == user.id)):
                        await s.delete(session)
            await s.commit()
            return user_out(user)

    # -- API keys -------------------------------------------------------------

    @r.get("/keys")
    async def list_keys(user: CurrentUser, request: Request, all: bool = False) -> list[dict[str, Any]]:
        if all and user.role != "admin":
            raise HTTPException(403, "Admins only")
        async with request.app.state.db.session() as s:
            q = select(ApiKey).order_by(ApiKey.created_at.desc())
            if not all:
                q = q.where(ApiKey.user_id == user.id)
            return [
                {"id": k.id, "name": k.name, "prefix": k.prefix, "user_email": k.user.email,
                 "created_at": iso(k.created_at), "last_used_at": iso(k.last_used_at),
                 "revoked_at": iso(k.revoked_at)}
                for k in await s.scalars(q)
            ]

    @r.post("/keys")
    async def create_key(body: KeyIn, user: CurrentUser, request: Request) -> dict[str, Any]:
        key, prefix, key_hash = new_api_key()
        async with request.app.state.db.session() as s:
            row = ApiKey(user_id=user.id, name=body.name, prefix=prefix, key_hash=key_hash)
            s.add(row)
            await s.commit()
        # The only time the full key exists outside the client: we keep only its hash.
        return {"id": row.id, "name": row.name, "prefix": prefix, "key": key, "mcp_url": f"{settings.public_url}/mcp"}

    @r.delete("/keys/{key_id}")
    async def revoke_key(key_id: int, user: CurrentUser, request: Request) -> dict[str, bool]:
        async with request.app.state.db.session() as s:
            key = await s.get(ApiKey, key_id)
            if key is None or (key.user_id != user.id and user.role != "admin"):
                raise HTTPException(404, "No such key")
            if key.revoked_at is None:
                key.revoked_at, key.revoked_by = utcnow(), user.id
                await s.commit()
        return {"ok": True}

    # -- connected apps (OAuth grants) ------------------------------------------

    @r.get("/oauth/grants")
    async def list_grants(user: CurrentUser, request: Request, all: bool = False) -> list[dict[str, Any]]:
        """Apps this person signed in to by browser. Admins may list everyone's with ?all=true."""
        if all and user.role != "admin":
            raise HTTPException(403, "Admins only")
        q = (select(OAuthGrant, OAuthClient, User)
             .join(OAuthClient, OAuthClient.client_id == OAuthGrant.client_id)
             .join(User, User.id == OAuthGrant.user_id)
             .where(OAuthGrant.revoked_at.is_(None)).order_by(OAuthGrant.authorized_at.desc()))
        if not all:
            q = q.where(OAuthGrant.user_id == user.id)
        async with request.app.state.db.session() as s:
            return [
                {"id": g.id, "client_id": c.client_id, "client_name": c.name, "user_email": u.email,
                 "created_at": iso(g.authorized_at), "last_used_at": iso(g.last_used_at)}
                for g, c, u in (await s.execute(q)).all()
            ]

    @r.delete("/oauth/grants/{grant_id}")
    async def disconnect_app(grant_id: int, user: CurrentUser, request: Request) -> dict[str, bool]:
        """Disconnect an app: its access and refresh tokens stop working on their next use."""
        async with request.app.state.db.session() as s:
            grant = await s.get(OAuthGrant, grant_id)
            if grant is None or grant.revoked_at is not None or (grant.user_id != user.id and user.role != "admin"):
                raise HTTPException(404, "No such connected app")
            await oauth.revoke_grant(s, grant.id)
            await s.commit()
        return {"ok": True}

    # -- servers --------------------------------------------------------------

    async def pending_counts(s: Any) -> dict[int, int]:
        rows = await s.scalars(select(Tool).where(Tool.present.is_(True)))
        counts: dict[int, int] = {}
        for t in rows:
            if pending_review(t):
                counts[t.server_id] = counts.get(t.server_id, 0) + 1
        return counts

    async def tool_counts(s: Any) -> dict[int, int]:
        rows = await s.execute(select(Tool.server_id, func.count()).where(Tool.present.is_(True)).group_by(Tool.server_id))
        return {k: v for k, v in rows.all()}

    @r.get("/servers")
    async def list_servers(_: CurrentUser, request: Request) -> list[dict[str, Any]]:
        async with request.app.state.db.session() as s:
            counts, pending = await tool_counts(s), await pending_counts(s)
            return [{**server_out(x, counts.get(x.id, 0)), "pending_tools": pending.get(x.id, 0)}
                    for x in await s.scalars(select(Server).order_by(Server.slug))]

    @r.post("/servers", status_code=201)
    async def create_server(body: ServerIn, _: AdminUser, request: Request) -> dict[str, Any]:
        await check_upstream_url(body.url, settings.allow_loopback_upstreams)
        credential_rules(body.auth_type, body.auth_header)
        gateway: Gateway = request.app.state.gateway
        async with request.app.state.db.session() as s:
            if await s.scalar(select(Server).where(Server.slug == body.slug)):
                raise HTTPException(409, f"Slug {body.slug!r} is taken")
            server = new_server(body, gateway.box)
            s.add(server)
            await s.commit()
        refreshed = await gateway.refresh_server(server.id)
        async with request.app.state.db.session() as s:
            return server_out(refreshed or server, (await tool_counts(s)).get(server.id, 0))

    @r.patch("/servers/{server_id}")
    async def update_server(server_id: int, body: ServerPatch, _: AdminUser, request: Request) -> dict[str, Any]:
        gateway: Gateway = request.app.state.gateway
        if body.url is not None:
            await check_upstream_url(body.url, settings.allow_loopback_upstreams)
        async with request.app.state.db.session() as s:
            server = await s.get(Server, server_id)
            if server is None:
                raise HTTPException(404, "No such server")
            if server.managed_by and (body.url is not None or body.auth_type is not None or body.credential or body.clear_credential):
                raise HTTPException(409, "This server belongs to a provisioned store; its connection is managed for you. You can still disable it.")
            connection_changed = False
            if body.name is not None:
                server.name = body.name
            if body.url is not None and body.url != server.url:
                server.url, connection_changed = body.url, True
            if body.auth_type is not None:
                server.auth_type, connection_changed = body.auth_type, True
            if body.auth_header is not None:
                server.auth_header, connection_changed = body.auth_header, True
            credential_rules(server.auth_type, server.auth_header)
            if body.clear_credential:
                server.credential_enc, connection_changed = None, True
            elif body.credential:
                server.credential_enc, connection_changed = gateway.box.seal(body.credential), True
            if body.timeout_seconds is not None:
                server.timeout_seconds = body.timeout_seconds
            if body.forward_identity is not None:
                server.forward_identity = body.forward_identity
            if body.enabled is not None:
                server.enabled = body.enabled
            if connection_changed:
                server.protocol_version = None
            await s.commit()
        if server.enabled and (connection_changed or body.enabled):
            await gateway.refresh_server(server_id)
        async with request.app.state.db.session() as s:
            server = await s.get(Server, server_id)
            return server_out(server, (await tool_counts(s)).get(server_id, 0))

    @r.delete("/servers/{server_id}")
    async def delete_server(server_id: int, _: AdminUser, request: Request) -> dict[str, bool]:
        async with request.app.state.db.session() as s:
            server = await s.get(Server, server_id, options=[selectinload(Server.tools)])
            if server is None:
                raise HTTPException(404, "No such server")
            if server.managed_by:
                raise HTTPException(409, "This server belongs to a provisioned store. Delete the store on the Stores page instead.")
            await s.delete(server)
            await s.commit()
        return {"ok": True}

    @r.post("/servers/{server_id}/refresh")
    async def refresh_server(server_id: int, _: AdminUser, request: Request) -> dict[str, Any]:
        server = await request.app.state.gateway.refresh_server(server_id)
        if server is None:
            raise HTTPException(404, "No such server")
        async with request.app.state.db.session() as s:
            return server_out(server, (await tool_counts(s)).get(server_id, 0))

    @r.get("/servers/{server_id}/tools")
    async def server_tools(server_id: int, _: AdminUser, request: Request) -> list[dict[str, Any]]:
        async with request.app.state.db.session() as s:
            server = await s.get(Server, server_id, options=[selectinload(Server.tools)])
            if server is None:
                raise HTTPException(404, "No such server")
            return [tool_out(server, t) for t in sorted(server.tools, key=lambda t: (not t.present, t.name))]

    @r.patch("/servers/{server_id}/tools/{tool_name}")
    async def set_tool_roles(server_id: int, tool_name: str, body: ToolRolesIn, _: AdminUser, request: Request) -> dict[str, Any]:
        async with request.app.state.db.session() as s:
            server = await s.get(Server, server_id)
            tool = await s.scalar(select(Tool).where(Tool.server_id == server_id, Tool.name == tool_name))
            if server is None or tool is None:
                raise HTTPException(404, "No such tool")
            tool.allowed_roles = [role for role in ROLES if role in body.allowed_roles]
            await s.commit()
            return tool_out(server, tool)

    @r.post("/servers/{server_id}/tools/{tool_name}/approve")
    async def approve_tool(server_id: int, tool_name: str, _: AdminUser, request: Request) -> dict[str, Any]:
        """Accept a tool's current definition: it is shown and callable again."""
        async with request.app.state.db.session() as s:
            server = await s.get(Server, server_id)
            tool = await s.scalar(select(Tool).where(Tool.server_id == server_id, Tool.name == tool_name))
            if server is None or tool is None:
                raise HTTPException(404, "No such tool")
            tool.approved_hash, tool.approved_definition = tool.definition_hash, definition_of(tool)
            await s.commit()
            return tool_out(server, tool)

    @r.get("/tools")
    async def my_tools(user: CurrentUser, request: Request) -> list[dict[str, Any]]:
        """What this user would see over MCP or in chat right now."""
        gateway: Gateway = request.app.state.gateway
        return [
            {"public_name": public_name(server.slug, tool.name), "server": server.name, "server_slug": server.slug,
             "server_status": server.status, "title": tool.title, "description": tool.description,
             "read_only": bool((tool.annotations or {}).get("readOnlyHint"))}
            for server, tool in await gateway.visible_tools(chat_identity(user))
        ]

    @r.post("/servers/test")
    async def test_server(body: ServerTestIn, _: AdminUser, request: Request) -> dict[str, Any]:
        """Try the connection the form describes, so the admin sees tools before saving.

        Nothing is written. The same URL and credential rules apply as on save, so
        a passing test means the save will pass too.
        """
        await check_upstream_url(body.url, settings.allow_loopback_upstreams)
        credential_rules(body.auth_type, body.auth_header)
        gateway: Gateway = request.app.state.gateway
        headers: dict[str, str] = {}
        secret = body.credential
        async with request.app.state.db.session() as s:
            if secret is None and body.server_id is not None:
                stored = await s.get(Server, body.server_id)
                if stored is not None and stored.credential_enc:
                    secret = gateway.box.open(stored.credential_enc)
            # Base tool names already offered by other servers, for the collision note.
            rows = await s.execute(
                select(Tool.name, Server.slug).join(Server, Server.id == Tool.server_id)
                .where(Tool.present.is_(True), Server.slug != body.slug)
            )
            elsewhere: dict[str, list[str]] = {}
            for tool_name, slug in rows.all():
                elsewhere.setdefault(tool_name, []).append(slug)
        if body.auth_type != "none" and secret:
            if body.auth_type == "bearer":
                headers["Authorization"] = f"Bearer {secret}"
            elif body.auth_header:
                headers[body.auth_header] = secret
        target = upstreams.Target(slug=body.slug, url=body.url, timeout=body.timeout_seconds, headers=headers,
                                  allow_loopback=settings.allow_loopback_upstreams)
        try:
            found = await upstreams.discover(target)
        except upstreams.UpstreamFailure as failure:
            return {"ok": False, "kind": failure.kind, "message": failure.message}
        return {
            "ok": True,
            "latency_ms": found.latency_ms,
            "protocol_version": found.protocol_version,
            "tools": [
                {"name": t.name, "public_name": public_name(body.slug, t.name), "title": t.title,
                 "read_only": bool(t.annotations and t.annotations.read_only_hint)}
                for t in found.tools
            ],
            "collisions": [
                {"name": t.name, "servers": sorted(elsewhere[t.name])} for t in found.tools if t.name in elsewhere
            ],
        }

    # -- audit ----------------------------------------------------------------

    @r.get("/audit")
    async def audit(
        user: CurrentUser,
        request: Request,
        user_id: int | None = None,
        server: str | None = None,
        outcome: str | None = None,
        via: Literal["mcp", "chat"] | None = None,
        chat_id: str | None = None,
        before_id: int | None = None,
        after_id: int | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
        if user.role != "admin":
            q = q.where(AuditEvent.user_id == user.id)  # members see their own calls only
        elif user_id is not None:
            q = q.where(AuditEvent.user_id == user_id)
        if server:
            q = q.where(AuditEvent.server_slug == server)
        if outcome:
            q = q.where(AuditEvent.outcome == outcome)
        if via:
            q = q.where(AuditEvent.via == via)
        if chat_id:
            q = q.where(AuditEvent.chat_id == chat_id)
        if before_id:
            q = q.where(AuditEvent.id < before_id)
        if after_id:
            # Live polling: only what arrived since the newest row the page has.
            q = q.where(AuditEvent.id > after_id)
        async with request.app.state.db.session() as s:
            return [
                {"id": e.id, "started_at": iso(e.started_at), "user_id": e.user_id, "user_email": e.user_email,
                 "via": e.via, "api_key_prefix": e.api_key_prefix, "server": e.server_slug, "tool": e.tool_name,
                 "public_name": e.public_name, "arguments": e.arguments, "outcome": e.outcome, "error": e.error,
                 "duration_ms": e.duration_ms, "chat_id": e.chat_id}
                for e in await s.scalars(q)
            ]

    @r.get("/audit/summary")
    async def audit_summary(
        user: CurrentUser,
        request: Request,
        hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    ) -> dict[str, Any]:
        """Per-server and per-hour counts for the Overview page.

        Computed in Python from one read of the window's rows (capped at 5000).
        At this gateway's volume that is cheaper to reason about than SQL
        percentiles, and it runs the same on SQLite and MariaDB.
        """
        now = utcnow()
        since = (now - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
        q = select(AuditEvent).where(AuditEvent.started_at >= since).order_by(AuditEvent.id.desc()).limit(5000)
        if user.role != "admin":
            q = q.where(AuditEvent.user_id == user.id)
        async with request.app.state.db.session() as s:
            rows = list(await s.scalars(q))

        def percentile(values: list[int], p: float) -> int | None:
            if not values:
                return None
            ordered = sorted(values)
            rank = max(1, int(round(p * len(ordered))))  # nearest rank
            return ordered[rank - 1]

        by_server: dict[str, list[AuditEvent]] = {}
        by_tool: dict[str, list[AuditEvent]] = {}
        by_hour: dict[datetime, list[AuditEvent]] = {}
        for e in rows:
            by_server.setdefault(e.server_slug or "?", []).append(e)
            by_tool.setdefault(e.public_name, []).append(e)
            by_hour.setdefault(e.started_at.replace(minute=0, second=0, microsecond=0), []).append(e)

        def summarize(events: list[AuditEvent]) -> dict[str, Any]:
            durations = [e.duration_ms for e in events]
            return {
                "calls": len(events),
                "errors": sum(1 for e in events if e.outcome != "ok"),
                "p50_ms": percentile(durations, 0.5),
                "p95_ms": percentile(durations, 0.95),
            }

        hour_starts = [since + timedelta(hours=i) for i in range(int((now - since).total_seconds() // 3600) + 1)]
        slowest = sorted(
            ({"public_name": name, "calls": len(evs), "p95_ms": percentile([e.duration_ms for e in evs], 0.95)}
             for name, evs in by_tool.items()),
            key=lambda t: -(t["p95_ms"] or 0),
        )
        return {
            "since": iso(since),
            "servers": [{"slug": slug, **summarize(evs)} for slug, evs in sorted(by_server.items())],
            "per_hour": [
                {"hour": iso(h), "calls": len(by_hour.get(h, [])),
                 "errors": sum(1 for e in by_hour.get(h, []) if e.outcome != "ok")}
                for h in hour_starts
            ],
            "slowest_tools": slowest[:5],
        }

    return r


def install(app: FastAPI, settings: Settings) -> None:
    app.include_router(build_router(settings))
