"""OAuth sign-in for MCP clients. The gateway is its own authorization server.

Claude Code, Cursor and other MCP clients can open a browser, let a person log
in, and get a token for `/mcp`, so nobody pastes an API key. API keys keep
working. The flow follows the MCP authorization spec:

1. A client calls `/mcp` and gets 401 with `resource_metadata="..."`.
2. It reads `/.well-known/oauth-protected-resource/mcp`, then
   `/.well-known/oauth-authorization-server`.
3. It registers itself at `/register` (RFC 7591) and sends the browser to
   `/authorize` with a PKCE challenge and `resource=<PUBLIC_URL>/mcp` (RFC 8707).
4. `authorize()` below saves the request and sends the browser to
   `/oauth/consent` (oauth_consent.py). The person logs in and presses Allow.
5. The client trades the code at `/token` for an access token (about an hour)
   and a refresh token (30 days, replaced each time it is used).

The MCP SDK supplies the HTTP endpoints (`create_auth_routes`) and checks PKCE,
redirect URIs and scopes. This file is the part the SDK leaves to us: storage,
single-use codes, tokens bound to one audience, refresh rotation, a cap on open
registration, and revoking a person's grant.

Codes and tokens are random, opaque and stored as SHA-256 hashes, like API keys.
A stolen database holds nothing a client can present.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import AnyHttpUrl, AnyUrl, ConfigDict, TypeAdapter
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mcp.server.auth.handlers.metadata import MetadataHandler, ProtectedResourceMetadataHandler
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import build_metadata, cors_middleware, create_auth_routes, validate_issuer_url
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import (
    InvalidScopeError,
    OAuthClientInformationFull,
    ProtectedResourceMetadata,
)
from mcp.shared.auth import OAuthToken as TokenResponse

from gateway.core import Identity
from gateway.db import Database, OAuthClient, OAuthCode, OAuthGrant, OAuthRequest, OAuthToken, User, utcnow
from gateway.security import sha256_hex

log = logging.getLogger("gateway.oauth")

SCOPE = "mcp:tools"  # use the gateway's tools as the person; their role still limits what that means
VALID_SCOPES = [SCOPE, "offline_access"]  # offline_access is accepted because clients add it; refresh tokens are always issued

ACCESS_PREFIX = "urumioat_"
REFRESH_PREFIX = "urumiort_"
CODE_PREFIX = "urumioc_"

CODE_TTL = timedelta(minutes=5)
REQUEST_TTL = timedelta(minutes=10)  # how long a person has to log in and press Allow
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
REFRESH_MAX = timedelta(days=90)  # a chain of refresh tokens ends here, however busy it is

# /register and /authorize need no login, so what they store is capped.
MAX_UNAPPROVED_CLIENTS = 200
UNAPPROVED_CLIENT_TTL = timedelta(hours=24)
MAX_PENDING_REQUESTS = 1000
MAX_REDIRECT_URIS = 5
MAX_REDIRECT_URI_LENGTH = 512
MAX_METADATA_BYTES = 8192
MAX_NAME_LENGTH = 100

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
# Private-use schemes (RFC 8252 7.1) for desktop apps. Cursor is the one we know of.
# Not tested against a real Cursor yet: see docs/design.md.
APP_SCHEMES = {"cursor"}
PKCE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")  # base64url of a SHA-256 digest, S256 only

_URL = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))


def _ts(value: datetime) -> float:
    return value.replace(tzinfo=UTC).timestamp()


def _new(prefix: str) -> tuple[str, str]:
    """Return (plaintext, hash). Only the hash is stored."""
    raw = prefix + secrets.token_urlsafe(32)
    return raw, sha256_hex(raw)


def display_name(name: str | None) -> str:
    """A client names itself, so treat it as text from a stranger: no control characters, short."""
    cleaned = " ".join("".join(ch if ch.isprintable() else " " for ch in (name or "")).split())
    return cleaned[:MAX_NAME_LENGTH] or "Unnamed app"


# ------------------------------------------------------------------ config


@dataclass(frozen=True)
class OAuthConfig:
    issuer_url: AnyHttpUrl
    # No trailing slash. Clients compare `iss` and the metadata `issuer` as plain strings (RFC 9207).
    issuer: str
    # The audience of every token: <PUBLIC_URL>/mcp (RFC 8707).
    resource: str
    # Where /mcp says the resource metadata is (RFC 9728), sent in WWW-Authenticate.
    metadata_url: str

    @classmethod
    def from_public_url(cls, public_url: str) -> OAuthConfig:
        """The SDK refuses plain http except for localhost; so do we, rather than send tokens in clear."""
        parts = urlsplit(public_url)
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError("PUBLIC_URL must be an origin such as https://host, with no path")
        url = _URL.validate_python(public_url)
        validate_issuer_url(url)
        issuer = str(url).rstrip("/")
        return cls(url, issuer, f"{issuer}/mcp", f"{issuer}/.well-known/oauth-protected-resource/mcp")


def canonical_resource(value: str) -> str:
    """`https://host/mcp/` and `https://host/mcp` name the same resource."""
    return value[:-1] if value.endswith("/") else value


# ---------------------------------------------------------------- the client


def _same_loopback(registered: AnyUrl, requested: AnyUrl) -> bool:
    """RFC 8252 7.3: a native app picks a free port each time, so the port of a loopback URI is not compared."""
    return (
        registered.scheme == requested.scheme == "http"
        and registered.host in LOOPBACK_HOSTS
        and registered.host == requested.host
        and registered.path == requested.path
        and registered.query == requested.query
    )


class GatewayClient(OAuthClientInformationFull):
    """A registered client as the SDK handlers see it, with two relaxations."""

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is not None and any(_same_loopback(r, redirect_uri) for r in self.redirect_uris or []):
            return redirect_uri
        return super().validate_redirect_uri(redirect_uri)

    def validate_scope(self, requested_scope: str | None) -> list[str] | None:
        # The scope names are fixed and the person's role does the real limiting, so any known scope is
        # fine. Clients differ on whether they register `offline_access` before they ask for it.
        if requested_scope is None:
            return None
        scopes = requested_scope.split()
        for scope in scopes:
            if scope not in VALID_SCOPES:
                raise InvalidScopeError(f"Unknown scope {scope}")
        return scopes


def redirect_problem(uri: AnyUrl) -> str | None:
    """Why a registered redirect URI is refused, or None."""
    if len(str(uri)) > MAX_REDIRECT_URI_LENGTH:
        return "redirect URI is too long"
    if uri.fragment or uri.username or uri.password:
        return "redirect URI must not have a fragment or credentials"
    scheme = uri.scheme.lower()
    if scheme == "https" and uri.host:
        return None
    if scheme == "http":
        return None if uri.host in LOOPBACK_HOSTS else "http redirect URIs are only allowed for localhost, 127.0.0.1 and [::1]"
    if scheme in APP_SCHEMES:
        return None
    return f"redirect URI scheme {scheme!r} is not allowed"


# ----------------------------------------------------------- stored objects


class GrantCode(AuthorizationCode):
    row_id: int
    grant_id: int


class GrantRefresh(RefreshToken):
    row_id: int
    grant_id: int


class GrantAccess(AccessToken):
    grant_id: int


async def revoke_grant_tokens(s: AsyncSession, grant_id: int) -> None:
    """Stop every token and unused code the grant issued. The caller commits."""
    now = utcnow()
    await s.execute(update(OAuthToken).where(OAuthToken.grant_id == grant_id, OAuthToken.revoked_at.is_(None)).values(revoked_at=now))
    await s.execute(update(OAuthCode).where(OAuthCode.grant_id == grant_id, OAuthCode.used_at.is_(None)).values(used_at=now))


async def revoke_grant(s: AsyncSession, grant_id: int) -> None:
    """End one person's approval of one app: everything it issued stops working now. The caller commits."""
    await s.execute(update(OAuthGrant).where(OAuthGrant.id == grant_id, OAuthGrant.revoked_at.is_(None)).values(revoked_at=utcnow()))
    await revoke_grant_tokens(s, grant_id)


class GatewayOAuthProvider:
    """Implements the SDK's `OAuthAuthorizationServerProvider` on the gateway database."""

    def __init__(self, db: Database, config: OAuthConfig) -> None:
        self.db = db
        self.config = config

    # -- clients -----------------------------------------------------------

    async def get_client(self, client_id: str) -> GatewayClient | None:
        async with self.db.session() as s:
            row = await s.get(OAuthClient, client_id)
            return GatewayClient.model_validate_json(row.info_json) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = client_info.redirect_uris or []
        if not uris or len(uris) > MAX_REDIRECT_URIS:
            raise RegistrationError("invalid_redirect_uri", f"give between 1 and {MAX_REDIRECT_URIS} redirect URIs")
        for uri in uris:
            if problem := redirect_problem(uri):
                raise RegistrationError("invalid_redirect_uri", problem)
        # Every client is public and relies on PKCE, so no client secret exists to store or leak. The SDK
        # sends this same object back as the registration response, so the client sees the change.
        client_info.token_endpoint_auth_method = "none"
        client_info.client_secret = None
        client_info.client_secret_expires_at = None
        client_info.client_name = display_name(client_info.client_name)
        info_json = client_info.model_dump_json(exclude_none=True)
        if len(info_json) > MAX_METADATA_BYTES:
            raise RegistrationError("invalid_client_metadata", "client metadata is too large")
        async with self.db.session() as s:
            await self._purge(s)
            s.add(OAuthClient(client_id=client_info.client_id, name=client_info.client_name, info_json=info_json))
            await s.commit()

    async def _purge(self, s: AsyncSession) -> None:
        """Drop what has expired, and keep the pile of never-approved clients under a cap."""
        now = utcnow()
        await s.execute(delete(OAuthRequest).where(OAuthRequest.expires_at < now))
        await s.execute(delete(OAuthCode).where(OAuthCode.expires_at < now - timedelta(days=1)))
        await s.execute(delete(OAuthToken).where(OAuthToken.expires_at < now - timedelta(days=1)))
        unapproved = OAuthClient.approved_at.is_(None)
        await self._delete_clients(s, select(OAuthClient.client_id).where(unapproved, OAuthClient.created_at < now - UNAPPROVED_CLIENT_TTL))
        count = await s.scalar(select(func.count()).select_from(OAuthClient).where(unapproved)) or 0
        # Room for the one being added: when full, the oldest unapproved clients go.
        excess = count - MAX_UNAPPROVED_CLIENTS + 1
        if excess > 0:
            await self._delete_clients(s, select(OAuthClient.client_id).where(unapproved).order_by(OAuthClient.created_at).limit(excess))

    @staticmethod
    async def _delete_clients(s: AsyncSession, ids: Any) -> None:
        doomed = list(await s.scalars(ids))
        if doomed:
            await s.execute(delete(OAuthRequest).where(OAuthRequest.client_id.in_(doomed)))
            await s.execute(delete(OAuthClient).where(OAuthClient.client_id.in_(doomed)))

    # -- authorization request ----------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Save the request and send the browser to the consent page. The code is issued there."""
        resource = canonical_resource(params.resource) if params.resource else self.config.resource
        if resource != self.config.resource:
            raise AuthorizeError("invalid_target", f"this server only issues tokens for {self.config.resource}")
        if not PKCE_CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError("invalid_request", "code_challenge must be a 43-character S256 challenge")
        scopes = [SCOPE] + (["offline_access"] if "offline_access" in (params.scopes or []) else [])
        raw, request_hash = _new("")
        async with self.db.session() as s:
            await self._purge(s)
            if (await s.scalar(select(func.count()).select_from(OAuthRequest)) or 0) >= MAX_PENDING_REQUESTS:
                raise AuthorizeError("temporarily_unavailable", "too many sign-ins are waiting; try again in a few minutes")
            s.add(OAuthRequest(
                request_hash=request_hash, client_id=client.client_id, redirect_uri=str(params.redirect_uri),
                redirect_explicit=params.redirect_uri_provided_explicitly, state=params.state, scopes=scopes,
                code_challenge=params.code_challenge, resource=resource, expires_at=utcnow() + REQUEST_TTL,
            ))
            await s.commit()
        return f"{self.config.issuer}/oauth/consent?request={raw}"

    # -- the consent page's side ----------------------------------------------

    async def load_request(self, raw_id: str | None) -> tuple[OAuthRequest, OAuthClient] | None:
        if not raw_id:
            return None
        async with self.db.session() as s:
            row = (await s.execute(
                select(OAuthRequest, OAuthClient)
                .join(OAuthClient, OAuthClient.client_id == OAuthRequest.client_id)
                .where(OAuthRequest.request_hash == sha256_hex(raw_id), OAuthRequest.expires_at > utcnow())
            )).first()
            return (row[0], row[1]) if row else None

    async def new_csrf(self, request_id: int) -> str:
        """A fresh token for the form being shown. Only its hash is kept; the form and a cookie carry it."""
        token, token_hash = _new("")
        async with self.db.session() as s:
            await s.execute(update(OAuthRequest).where(OAuthRequest.id == request_id).values(csrf_hash=token_hash))
            await s.commit()
        return token

    def denied_url(self, req: OAuthRequest) -> str:
        return construct_redirect_uri(req.redirect_uri, error="access_denied", error_description="The user denied the request",
                                      state=req.state, iss=self.config.issuer)

    async def deny(self, req: OAuthRequest) -> str | None:
        """Close the request and say no. None if someone already closed it."""
        async with self.db.session() as s:
            gone = await s.execute(delete(OAuthRequest).where(OAuthRequest.id == req.id))
            await s.commit()
            return self.denied_url(req) if gone.rowcount == 1 else None  # type: ignore[attr-defined]

    async def approve(self, req: OAuthRequest, user: User) -> str | None:
        """Close the request and issue a code for `user`. None if someone already closed it.

        One grant exists per person and app. Approving again replaces the earlier tokens, so "connected"
        always means the latest approval.
        """
        raw, code_hash = _new(CODE_PREFIX)
        now = utcnow()
        async with self.db.session() as s:
            claimed = await s.execute(delete(OAuthRequest).where(OAuthRequest.id == req.id))
            if claimed.rowcount != 1:  # type: ignore[attr-defined]
                return None
            grant = await s.scalar(select(OAuthGrant).where(
                OAuthGrant.user_id == user.id, OAuthGrant.client_id == req.client_id, OAuthGrant.revoked_at.is_(None)))
            if grant is None:
                grant = OAuthGrant(user_id=user.id, client_id=req.client_id)
                s.add(grant)
            else:
                await revoke_grant_tokens(s, grant.id)  # the earlier approval's tokens end; the grant carries on
            grant.authorized_at = now
            await s.execute(update(OAuthClient).where(OAuthClient.client_id == req.client_id, OAuthClient.approved_at.is_(None))
                            .values(approved_at=now))
            await s.flush()
            s.add(OAuthCode(
                code_hash=code_hash, grant_id=grant.id, client_id=req.client_id, user_id=user.id,
                redirect_uri=req.redirect_uri, redirect_explicit=req.redirect_explicit, scopes=req.scopes,
                code_challenge=req.code_challenge, resource=req.resource, expires_at=now + CODE_TTL,
            ))
            await s.commit()
        log.info("oauth: %s approved client %s", user.email, req.client_id)
        return construct_redirect_uri(req.redirect_uri, code=raw, state=req.state, iss=self.config.issuer)

    # -- authorization code ---------------------------------------------------

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> GrantCode | None:
        async with self.db.session() as s:
            row = await s.scalar(select(OAuthCode).where(
                OAuthCode.code_hash == sha256_hex(authorization_code), OAuthCode.client_id == client.client_id))
            if row is None:
                return None
            return GrantCode(
                code=authorization_code, scopes=row.scopes, expires_at=_ts(row.expires_at), client_id=row.client_id,
                code_challenge=row.code_challenge, redirect_uri=AnyUrl(row.redirect_uri),
                redirect_uri_provided_explicitly=row.redirect_explicit, resource=row.resource, subject=str(row.user_id),
                row_id=row.id, grant_id=row.grant_id,
            )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: GrantCode) -> TokenResponse:
        async with self.db.session() as s:
            # Mark it used first, in one statement, so two requests with the same code cannot both win.
            claimed = await s.execute(update(OAuthCode).where(OAuthCode.id == authorization_code.row_id, OAuthCode.used_at.is_(None))
                                      .values(used_at=utcnow()))
            if claimed.rowcount != 1:  # type: ignore[attr-defined]
                # A code came back twice. Whoever holds the first answer is suspect, so end that grant.
                log.warning("oauth: authorization code used twice; ending grant %s", authorization_code.grant_id)
                await revoke_grant(s, authorization_code.grant_id)
                await s.commit()
                raise TokenError("invalid_grant", "authorization code was already used")
            grant = await self._live_grant(s, authorization_code.grant_id)
            if grant is None:
                await s.commit()
                raise TokenError("invalid_grant", "this authorization is no longer valid")
            tokens = self._issue_pair(s, grant, authorization_code.scopes, authorization_code.resource)
            await s.commit()
        return tokens

    # -- refresh token --------------------------------------------------------

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> GrantRefresh | None:
        async with self.db.session() as s:
            row = await s.scalar(select(OAuthToken).where(
                OAuthToken.token_hash == sha256_hex(refresh_token), OAuthToken.kind == "refresh",
                OAuthToken.client_id == client.client_id, OAuthToken.revoked_at.is_(None)))
            if row is None:
                return None
            # An already used token is returned on purpose: exchanging it is how reuse is noticed.
            return GrantRefresh(
                token=refresh_token, client_id=row.client_id, scopes=row.scopes, expires_at=int(_ts(row.expires_at)),
                resource=row.resource, subject=str(row.user_id), row_id=row.id, grant_id=row.grant_id,
            )

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: GrantRefresh, scopes: list[str]) -> TokenResponse:
        async with self.db.session() as s:
            claimed = await s.execute(update(OAuthToken).where(
                OAuthToken.id == refresh_token.row_id, OAuthToken.used_at.is_(None), OAuthToken.revoked_at.is_(None)
            ).values(used_at=utcnow()))
            if claimed.rowcount != 1:  # type: ignore[attr-defined]
                # A replaced refresh token came back: it was copied, or two copies of the client ran at once.
                # Ending the grant makes the person sign in again; a thief gets nothing.
                log.warning("oauth: replaced refresh token presented again; ending grant %s", refresh_token.grant_id)
                await revoke_grant(s, refresh_token.grant_id)
                await s.commit()
                raise TokenError("invalid_grant", "refresh token was already used")
            grant = await self._live_grant(s, refresh_token.grant_id)
            if grant is None or utcnow() >= grant.authorized_at + REFRESH_MAX:
                await s.commit()
                raise TokenError("invalid_grant", "sign in again")
            tokens = self._issue_pair(s, grant, scopes or refresh_token.scopes, refresh_token.resource)
            await s.commit()
        return tokens

    # -- access token ----------------------------------------------------------

    async def load_access_token(self, token: str) -> GrantAccess | None:
        async with self.db.session() as s:
            found = await self._live_access(s, token)
            if found is None:
                return None
            row, grant, user, _ = found
            return GrantAccess(
                token=token, client_id=row.client_id, scopes=row.scopes, expires_at=int(_ts(row.expires_at)),
                resource=row.resource, subject=str(user.id), grant_id=grant.id,
            )

    async def identify(self, token: str) -> Identity | None:
        """Who is behind this access token, or None. Called on every /mcp request, so revoking is immediate."""
        async with self.db.session() as s:
            found = await self._live_access(s, token)
            if found is None:
                return None
            row, grant, user, client = found
            # Only a token meant for this server: a token issued for another resource is not ours to accept.
            if row.resource not in (None, self.config.resource) or SCOPE not in row.scopes:
                return None
            now = utcnow()
            if grant.last_used_at is None or now - grant.last_used_at > timedelta(seconds=60):
                grant.last_used_at = now  # throttled, like API keys
                await s.commit()
            return Identity(
                user_id=user.id, email=user.email, role=user.role,  # type: ignore[arg-type]
                via="mcp", api_key_prefix=f"oauth:{client.name}"[:32], name=user.name,
            )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """RFC 7009. Revoking either token of a grant ends the whole grant."""
        async with self.db.session() as s:
            row = await s.scalar(select(OAuthToken).where(OAuthToken.token_hash == sha256_hex(token.token)))
            if row is not None:
                await revoke_grant(s, row.grant_id)
                await s.commit()

    # -- helpers ---------------------------------------------------------------

    async def _live_access(self, s: AsyncSession, raw: str) -> tuple[OAuthToken, OAuthGrant, User, OAuthClient] | None:
        row = (await s.execute(
            select(OAuthToken, OAuthGrant, User, OAuthClient)
            .join(OAuthGrant, OAuthGrant.id == OAuthToken.grant_id)
            .join(User, User.id == OAuthGrant.user_id)
            .join(OAuthClient, OAuthClient.client_id == OAuthGrant.client_id)
            .where(OAuthToken.token_hash == sha256_hex(raw), OAuthToken.kind == "access")
        )).first()
        if row is None:
            return None
        token, grant, user, client = row
        if token.revoked_at is not None or token.expires_at <= utcnow() or grant.revoked_at is not None or not user.active:
            return None
        return token, grant, user, client

    async def _live_grant(self, s: AsyncSession, grant_id: int) -> OAuthGrant | None:
        row = (await s.execute(
            select(OAuthGrant, User).join(User, User.id == OAuthGrant.user_id).where(OAuthGrant.id == grant_id)
        )).first()
        if row is None or row[0].revoked_at is not None or not row[1].active:
            return None
        return row[0]

    def _issue_pair(self, s: AsyncSession, grant: OAuthGrant, scopes: list[str], resource: str | None) -> TokenResponse:
        now = utcnow()
        access, access_hash = _new(ACCESS_PREFIX)
        refresh, refresh_hash = _new(REFRESH_PREFIX)
        common = {"grant_id": grant.id, "client_id": grant.client_id, "user_id": grant.user_id, "scopes": scopes, "resource": resource}
        s.add(OAuthToken(token_hash=access_hash, kind="access", expires_at=now + ACCESS_TTL, **common))
        s.add(OAuthToken(token_hash=refresh_hash, kind="refresh", expires_at=min(now + REFRESH_TTL, grant.authorized_at + REFRESH_MAX), **common))
        return TokenResponse(access_token=access, token_type="Bearer", expires_in=int(ACCESS_TTL.total_seconds()),
                             scope=" ".join(scopes), refresh_token=refresh)


# ------------------------------------------------------------------- routes


class FormGuard:
    """Sits in front of the SDK's form endpoints (/token and /revoke) for two things the SDK does not do.

    * `resource` (RFC 8707): a request that names another resource gets `invalid_target`. The SDK's token
      handler never shows the provider the `resource` of a token request. Tokens carry the audience stored
      with the authorization code, so this is a clear early answer, not the only check.
    * `fill_client_secret`: the SDK's revoke handler requires a `client_secret` field, which a public client
      has no value for (RFC 7009 does not require one). A missing field becomes an empty one.
    """

    def __init__(self, app: ASGIApp, *, resource: str | None = None, fill_client_secret: bool = False) -> None:
        self.app = app
        self.resource = resource
        self.fill_client_secret = fill_client_secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":  # the client went away
                return
            body += message.get("body", b"")
            if len(body) > 64 * 1024:
                await self._refuse(scope, receive, send, "invalid_request", "request body is too large")
                return
            if not message.get("more_body"):
                break
        form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        if self.resource and any(canonical_resource(r) != self.resource for r in form.get("resource", [])):
            await self._refuse(scope, receive, send, "invalid_target", f"this server only issues tokens for {self.resource}")
            return
        if self.fill_client_secret and "client_secret" not in form:
            body += (b"&" if body else b"") + b"client_secret="
            scope = {**scope, "headers": [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
                     + [(b"content-length", str(len(body)).encode())]}

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, error: str, description: str) -> None:
        response = JSONResponse({"error": error, "error_description": description}, status_code=400,
                                headers={"Cache-Control": "no-store", "Pragma": "no-cache", "Access-Control-Allow-Origin": "*"})
        await response(scope, receive, send)


def auth_routes(provider: GatewayOAuthProvider) -> list[BaseRoute]:
    """The OAuth endpoints: SDK handlers on our provider, with the corrections this deployment needs."""
    config = provider.config
    registration = ClientRegistrationOptions(enabled=True, valid_scopes=VALID_SCOPES, default_scopes=[SCOPE])
    revocation = RevocationOptions(enabled=True)

    metadata = build_metadata(config.issuer_url, None, registration, revocation)
    # Every client is public (see register_client), and the SDK's list leaves "none" out.
    metadata.token_endpoint_auth_methods_supported = ["none"]
    metadata.revocation_endpoint_auth_methods_supported = ["none"]
    metadata.authorization_response_iss_parameter_supported = True  # consent.py adds `iss` to every redirect

    resource_metadata = ProtectedResourceMetadata(
        resource=config.resource, authorization_servers=[config.issuer], scopes_supported=[SCOPE], resource_name="Urumi MCP gateway",
    )
    protected_resource = cors_middleware(ProtectedResourceMetadataHandler(resource_metadata).handle, ["GET", "OPTIONS"])

    routes: list[BaseRoute] = []
    for route in create_auth_routes(provider, config.issuer_url, client_registration_options=registration, revocation_options=revocation):
        if not isinstance(route, Route):
            routes.append(route)
        elif route.path == "/.well-known/oauth-authorization-server":
            continue  # replaced below
        elif route.path == "/token":
            routes.append(Route("/token", FormGuard(route.app, resource=config.resource), methods=sorted(route.methods or [])))
        elif route.path == "/revoke":
            routes.append(Route("/revoke", FormGuard(route.app, fill_client_secret=True), methods=sorted(route.methods or [])))
        else:
            routes.append(route)
    routes.append(Route("/.well-known/oauth-authorization-server", cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
                        methods=["GET", "OPTIONS"]))
    # Clients try the path form first, then the root form (RFC 9728 3.1). Both say the same thing.
    for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
        routes.append(Route(path, protected_resource, methods=["GET", "OPTIONS"]))
    return routes
