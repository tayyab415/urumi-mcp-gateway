"""The gateway's MCP *server* side: the one endpoint external clients connect to.

Every HTTP request to /mcp passes through `ApiKeyAuth` first. It resolves the
API key to a person and attaches that identity to the request. The MCP handlers
then read the identity from the request, never from anything the client sends
inside the MCP message.

Because the key is checked on every HTTP request, not once per connection, a
revoked key fails on its very next call, even from a client that connected
before the revocation.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import mcp_types as types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from sqlalchemy import select
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway.core import Gateway, Identity
from gateway.db import ApiKey, Database, LoginSession, User, utcnow
from gateway.security import ChatTokens, sha256_hex

# JSON-RPC error code for "no valid credential", sent with HTTP 401. JSON-RPC
# leaves -32000 to -32099 to servers, and the MCP SDK already uses some of them:
# -32001 is its "request timed out". A client that read our 401 as a timeout
# would report "the call may have run" when it was refused, so ours is distinct.
NOT_AUTHENTICATED = -32030

INSTRUCTIONS = (
    "Urumi team gateway. Tools from several connected servers, named <server>__<tool>. "
    "What you can see and call depends on your role. Every call is recorded under your user."
)


def build_server(gateway: Gateway) -> Server:
    def identity(ctx: Any) -> Identity:
        who = getattr(getattr(ctx.request, "state", None), "identity", None)
        if not isinstance(who, Identity):  # unreachable behind ApiKeyAuth; fail closed anyway
            raise MCPError(code=NOT_AUTHENTICATED, message="Not authenticated")
        return who

    async def list_tools(ctx: Any, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=await gateway.list_tools(identity(ctx)))

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await gateway.call_tool(identity(ctx), params.name, params.arguments)

    return Server(
        "urumi-gateway",
        version="0.1.0",
        title="Urumi MCP gateway",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def session_manager(server: Server) -> StreamableHTTPSessionManager:
    # Stateless: each POST stands alone, so there is no per-connection state to
    # outlive a revocation, and a restarted gateway Pod loses nothing.
    # DNS-rebinding protection is for browser-reachable localhost servers. This
    # endpoint accepts only API keys in headers, which a hostile page cannot read.
    return StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


async def authenticate_api_key(db: Database, key: str) -> Identity | None:
    async with db.session() as s:
        row = await s.scalar(select(ApiKey).join(User, ApiKey.user_id == User.id).where(ApiKey.key_hash == sha256_hex(key)))
        if row is None or row.revoked_at is not None or not row.user.active:
            return None
        now = utcnow()
        if row.last_used_at is None or now - row.last_used_at > timedelta(seconds=60):
            row.last_used_at = now  # throttled so busy clients do not write on every call
            await s.commit()
        return Identity(
            user_id=row.user.id,
            email=row.user.email,
            role=row.user.role,  # type: ignore[arg-type]
            via="mcp",
            api_key_prefix=row.prefix,
            name=row.user.name,
        )


async def authenticate_chat_token(db: Database, tokens: ChatTokens, token: str, chat_id: str | None) -> Identity | None:
    session_id = tokens.session_id(token)
    if session_id is None:
        return None
    async with db.session() as s:
        session = await s.get(LoginSession, session_id)
        if (
            session is None
            or session.expires_at < utcnow()
            or not session.user.active
            or not tokens.verify(token, session.id, session.token_hash)
        ):
            return None
        return Identity(
            user_id=session.user.id,
            email=session.user.email,
            role=session.user.role,  # type: ignore[arg-type]
            via="chat",
            name=session.user.name,
            # The chat service may label calls with its conversation id. It only
            # groups audit rows; it grants nothing.
            chat_id=(chat_id or "")[:64] or None,
        )


def _header(scope: Scope, name: bytes) -> str | None:
    return next((v.decode("latin-1") for k, v in scope["headers"] if k.lower() == name), None)


def _key_from(scope: Scope) -> str | None:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip() or None
    return headers.get("x-api-key") or None


class ApiKeyAuth:
    """Accepts a user's API key (external clients) or a chat token (dashboard chat)."""

    def __init__(self, app: ASGIApp, db: Database, chat_tokens: ChatTokens):
        self.app = app
        self.db = db
        self.chat_tokens = chat_tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        key = _key_from(scope)
        who = None
        if key and ChatTokens.session_id(key) is not None:
            who = await authenticate_chat_token(self.db, self.chat_tokens, key, _header(scope, b"x-urumi-chat-id"))
        elif key:
            who = await authenticate_api_key(self.db, key)
        if who is None:
            message = "Missing API key" if not key else "Invalid or revoked API key"
            body = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": NOT_AUTHENTICATED, "message": message}}).encode()
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Bearer realm="urumi-gateway"'),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        scope.setdefault("state", {})["identity"] = who
        await self.app(scope, receive, send)


def mcp_asgi_app(gateway: Gateway, db: Database, chat_tokens: ChatTokens) -> tuple[ASGIApp, StreamableHTTPSessionManager]:
    manager = session_manager(build_server(gateway))
    return ApiKeyAuth(StreamableHTTPASGIApp(manager), db, chat_tokens), manager
