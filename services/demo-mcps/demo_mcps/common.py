"""Shared plumbing for the demo MCP servers.

Two things here exist for the assignment's demonstrations:

* A static credential check. The gateway stores each upstream's credential and
  sends it on every call; users never see it. Shipping expects a bearer token,
  supplier expects a custom header, so both gateway credential styles get used.
* An artificial delay (ARTIFICIAL_DELAY_SECONDS). Setting it makes this server
  slower than the gateway's timeout, to show that one slow upstream does not
  stall discovery or calls to the others.
"""

from __future__ import annotations

import hmac
import logging
import os

import anyio
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("demo_mcps")


class StaticCredential:
    """ASGI middleware: reject MCP requests that lack the expected header value."""

    def __init__(self, app: ASGIApp, *, header: str, expected: str | None):
        self.app = app
        self.header = header.lower().encode()
        self.expected = expected.encode() if expected else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.expected is None or scope["path"] == "/healthz":
            await self.app(scope, receive, send)
            return
        supplied = next((v for k, v in scope["headers"] if k == self.header), b"")
        if not hmac.compare_digest(supplied, self.expected):
            response = JSONResponse({"error": "missing or invalid credential"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class ArtificialDelay:
    def __init__(self, app: ASGIApp, seconds: float):
        self.app = app
        self.seconds = seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.seconds > 0 and scope["path"] != "/healthz":
            await anyio.sleep(self.seconds)
        await self.app(scope, receive, send)


def serve(mcp: MCPServer, *, header: str, credential_env: str, value_prefix: str = "") -> ASGIApp:
    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app: ASGIApp = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host="0.0.0.0",
    )
    delay = float(os.environ.get("ARTIFICIAL_DELAY_SECONDS", "0") or 0)
    if delay > 0:
        log.warning("ARTIFICIAL_DELAY_SECONDS=%s: every MCP request will be delayed", delay)
        app = ArtificialDelay(app, delay)
    secret = os.environ.get(credential_env)
    if not secret:
        log.warning("%s is not set: this server accepts unauthenticated requests", credential_env)
    return StaticCredential(app, header=header, expected=f"{value_prefix}{secret}" if secret else None)
