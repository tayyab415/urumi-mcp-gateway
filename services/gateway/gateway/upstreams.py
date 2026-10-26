"""The gateway's MCP *client* side: talking to one upstream server.

Each discovery or call opens a fresh connection, does its work, and closes it.
That costs a round trip or two, and buys simplicity: there is no pooled session
to go stale when an upstream Pod is replaced, and no state shared between users.

Every exchange runs under a hard deadline (`anyio.fail_after`). Failures are
sorted into kinds the rest of the gateway can explain to a person or a model:

    unreachable   could not connect; the upstream never saw the request
    timeout       sent, but no answer before the deadline; the outcome is unknown
    auth_failed   the upstream rejected the credential the gateway stored for it
    protocol      the upstream answered with something that is not a valid MCP reply
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import anyio
import httpx2
import mcp_types as types
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

FailureKind = Literal["unreachable", "timeout", "auth_failed", "protocol"]

T = TypeVar("T")

CLIENT_INFO = types.Implementation(name="urumi-gateway", version="0.1.0")

# Building a TLS context loads the CA bundle, which costs 150-450 ms. Each call
# opens a new HTTP client, so they all share this one context.
_TLS = ssl.create_default_context()


class UpstreamFailure(Exception):
    def __init__(self, kind: FailureKind, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Target:
    """Everything needed to reach one upstream. Built per call from the database row."""

    slug: str
    url: str
    timeout: float
    headers: dict[str, str] = field(default_factory=dict, repr=False)  # holds the credential
    protocol_version: str | None = None


@dataclass
class Discovery:
    tools: list[types.Tool]
    protocol_version: str
    latency_ms: int


def _mode(target: Target) -> str:
    # Reuse what discovery learned, to skip the version probe on every call.
    if target.protocol_version in MODERN_PROTOCOL_VERSIONS:
        return target.protocol_version  # type: ignore[return-value]
    if target.protocol_version in HANDSHAKE_PROTOCOL_VERSIONS:
        return "legacy"
    return "auto"


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    return [exc]


def _classify(exc: Exception, statuses: list[int], target: Target) -> UpstreamFailure:
    leaves = _leaves(exc)
    if any(s in (401, 403) for s in statuses):
        return UpstreamFailure("auth_failed", f"{target.slug} rejected the gateway's stored credential (HTTP {statuses[-1]}).")
    for leaf in leaves:
        chain: BaseException | None = leaf
        while chain is not None:
            if isinstance(chain, (httpx2.ConnectError, httpx2.ConnectTimeout, ConnectionError)):
                return UpstreamFailure("unreachable", f"{target.slug} is unreachable ({chain.__class__.__name__}). The request was not delivered.")
            chain = chain.__cause__
    for leaf in leaves:
        if isinstance(leaf, MCPError):
            return UpstreamFailure("protocol", f"{target.slug} returned an MCP error: {leaf.message}")
    first = leaves[0] if leaves else exc
    return UpstreamFailure("protocol", f"{target.slug} failed: {first.__class__.__name__}: {str(first)[:200]}")


async def _exchange(target: Target, work: Callable[[Client], Awaitable[T]]) -> tuple[T, str]:
    statuses: list[int] = []

    async def record(response: httpx2.Response) -> None:
        statuses.append(response.status_code)

    try:
        with anyio.fail_after(target.timeout):
            async with httpx2.AsyncClient(
                headers=target.headers,
                verify=_TLS,
                # The deadline above is the real limit; this only stops a socket hanging past it.
                timeout=httpx2.Timeout(target.timeout + 5),
                event_hooks={"response": [record]},
            ) as http:
                transport = streamable_http_client(target.url, http_client=http)
                async with Client(transport, mode=_mode(target), cache=None, client_info=CLIENT_INFO) as client:  # type: ignore[arg-type]
                    result = await work(client)
                    return result, client.protocol_version
    except TimeoutError:
        raise UpstreamFailure("timeout", f"{target.slug} did not answer within {target.timeout:g}s.") from None
    except Exception as exc:  # ExceptionGroups from the transport's task group land here
        raise _classify(exc, statuses, target) from exc


async def discover(target: Target) -> Discovery:
    """List all of an upstream's tools, following pagination."""

    async def work(client: Client) -> list[types.Tool]:
        tools: list[types.Tool] = []
        cursor: str | None = None
        for _ in range(50):  # a server that never stops paginating is a bug, not a catalog
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
        return tools

    started = time.monotonic()
    tools, version = await _exchange(target, work)
    return Discovery(tools=tools, protocol_version=version, latency_ms=int((time.monotonic() - started) * 1000))


async def call_tool(target: Target, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    async def work(client: Client) -> types.CallToolResult:
        return await client.call_tool(name, arguments)

    result, _ = await _exchange(target, work)
    return result
