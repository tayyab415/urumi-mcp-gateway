"""The chat agent's only way to reach tools: an MCP client to the gateway.

Chat connects to the same MCP endpoint an external client uses, with the
signed-in user's credential. So the gateway applies the same checks to both:
who the user is, which servers are enabled, what the user may call, and the
audit record. Chat never decides any of that itself.

The Agents SDK does the MCP work. `GatewayServer` adds our rules on top:

- Every request carries the user's credential.
- A connection lasts one user turn, and lists tools once at the start. A
  server an admin disabled a minute ago is gone on the next message.
- A failed call is never retried. A retried `create_product` could create
  two products.
- Every failure becomes a tool result the model can read, worded by what we
  actually know: the call was refused (it did not run), or the outcome is
  unknown (it may have run).
- A tool that can change data waits for the user first (`approvals.py`). The
  gateway does not see the call until the user approves it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import mcp_types as types
from agents.exceptions import UserError
from agents.mcp import MCPServerStreamableHttp
from agents.mcp.util import MCPToolCustomDataContext, MCPToolMetaContext
from mcp.shared.exceptions import MCPError

from .approvals import ApprovalGate

log = logging.getLogger(__name__)

# The SDK gives `call_tool` no call id, so one rides in the request `_meta`
# under this key and is taken out again before the request leaves.
_CALL_ID_META = "urumi_call_id"

# Called the moment a tool call is known, before any waiting or any request.
Announce = Callable[[str, str, dict[str, Any]], None]


class GatewayUnavailable(Exception):
    """Chat could not open a session with the gateway, or list its tools."""


class _AuthWatch:
    """Remembers whether the gateway answered 401/403 on this connection.

    The MCP SDK reports an HTTP error status as a generic protocol error, so
    chat watches the responses itself to tell "your key was revoked" apart
    from other failures.
    """

    def __init__(self) -> None:
        self.rejected = False

    async def on_response(self, response: httpx2.Response) -> None:
        if response.status_code in (401, 403):
            self.rejected = True


_UNKNOWN_OUTCOME = (
    "{reason}. The outcome is unknown: if this tool changes data, it may have run. "
    "Do not call it again. Tell the user, and offer to check the current state instead."
)


class GatewayServer(MCPServerStreamableHttp):
    def __init__(self, url: str, credential: str, timeout_seconds: float, chat_id: str | None = None):
        self.auth = _AuthWatch()
        # The conversation id labels this turn's audit rows, so "Open run" in the
        # dashboard can show exactly the calls one chat made.
        headers = {"Authorization": f"Bearer {credential}"}
        if chat_id:
            headers["X-Urumi-Chat-Id"] = chat_id
        # Set per turn by the agent. Without a gate, nothing waits (CLI, tests).
        self.gate: ApprovalGate | None = None
        self.announce: Announce | None = None
        # What the gate decided per call id, read back when the SDK reports the output.
        self._verdicts: dict[str, dict[str, Any]] = {}
        super().__init__(
            params={
                "url": url,
                "headers": headers,
                "timeout": timeout_seconds,
                "sse_read_timeout": timeout_seconds + 5,
                "httpx_client_factory": self._http_client,
            },
            name="gateway",
            # This object lives for one turn, so caching means "list once per turn".
            cache_tools_list=True,
            client_session_timeout_seconds=timeout_seconds,
            max_retry_attempts=0,
            custom_data_extractor=self._custom_data,
            tool_meta_resolver=_call_id_meta,
        )

    def _http_client(self, headers: dict[str, str] | None = None, timeout: Any = None, auth: Any = None) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=False,
            event_hooks={"response": [self.auth.on_response]},
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None, meta: dict[str, Any] | None = None) -> types.CallToolResult:
        call_id, meta = _take_call_id(meta)
        arguments = arguments or {}
        if self.announce is not None:
            self.announce(call_id, tool_name, arguments)
        if self.gate is not None and not await self._is_read_only(tool_name):
            tool = await self._tool(tool_name)
            verdict = await self.gate.ask(
                call_id=call_id, name=tool_name, arguments=arguments, summary=_summary(tool_name, tool),
                server=_server_slug(tool_name), server_name=_server_name(tool_name, tool),
            )
            self._verdicts[call_id] = {"waited_ms": verdict.waited_ms}
            if not verdict.approved:
                self._verdicts[call_id].update({"denied": True, "timed_out": verdict.decision == "timeout"})
                return types.CallToolResult(content=[types.TextContent(type="text", text=verdict.refusal)], is_error=True)
        try:
            return await super().call_tool(tool_name, arguments, meta)
        except Exception as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=self._describe_failure(exc))], is_error=True)

    async def _tool(self, name: str) -> types.Tool | None:
        return next((t for t in await self.list_tools() if t.name == name), None)

    async def _is_read_only(self, name: str) -> bool:
        """Only a tool that says it is read-only skips the gate. No hint means it may change data."""
        tool = await self._tool(name)
        return bool(tool and tool.annotations and tool.annotations.read_only_hint)

    def _custom_data(self, ctx: MCPToolCustomDataContext) -> dict[str, Any]:
        # Rides on the SDK's tool-output item, so the UI can mark failed and denied calls.
        call_id = getattr(ctx.run_context, "tool_call_id", None)
        return {"is_error": bool(ctx.is_error), **self._verdicts.pop(call_id, {})}

    def _describe_failure(self, exc: BaseException) -> str:
        cause = _root_cause(exc)
        if isinstance(cause, MCPError) and cause.code == types.REQUEST_TIMEOUT:
            return _UNKNOWN_OUTCOME.format(reason="No answer from the gateway in time")
        if isinstance(cause, MCPError) and cause.code == types.CONNECTION_CLOSED:
            return _UNKNOWN_OUTCOME.format(reason="The connection to the gateway closed")
        if self.auth.rejected:
            return "The gateway no longer accepts this user's credential. The call did not run."
        if isinstance(cause, MCPError):
            # Any other protocol error is the gateway refusing the call, such as a
            # tool this user cannot use ("Unknown tool: ..."). It did not run.
            return f"The gateway rejected the call: {cause.message}. It did not run."
        return _UNKNOWN_OUTCOME.format(reason=f"Lost the connection to the gateway ({type(cause).__name__})")


def _call_id_meta(ctx: MCPToolMetaContext) -> dict[str, Any] | None:
    call_id = getattr(ctx.run_context, "tool_call_id", None)
    return {_CALL_ID_META: call_id} if call_id else None


def _take_call_id(meta: dict[str, Any] | None) -> tuple[str, dict[str, Any] | None]:
    if not meta or _CALL_ID_META not in meta:
        return f"local-{int(time.monotonic() * 1000)}", meta
    rest = {k: v for k, v in meta.items() if k != _CALL_ID_META}
    return str(meta[_CALL_ID_META]), rest or None


def _server_slug(public_name: str) -> str:
    return public_name.split("__", 1)[0]


# The gateway names each tool's server in the tool's _meta, as {"slug": ..., "name": ...}.
SERVER_META = "urumi.io/server"


def _server_name(public_name: str, tool: types.Tool | None) -> str:
    """The display name of the server a tool belongs to, e.g. "WooCommerce" or "Store: North"."""
    server = ((tool.meta or {}) if tool else {}).get(SERVER_META)
    if isinstance(server, dict) and isinstance(server.get("name"), str) and server["name"]:
        return server["name"]
    # A gateway without _meta: its titles are "<Server name>: <tool title>". A store's
    # server name has a colon of its own ("Store: North"), so the tool title is the
    # part after the last one.
    if tool and tool.title and ": " in tool.title:
        return tool.title.rsplit(": ", 1)[0]
    return _server_slug(public_name)


def _summary(public_name: str, tool: types.Tool | None) -> str:
    """"Run woo__update_order_status on WooCommerce"."""
    return f"Run {public_name} on {_server_name(public_name, tool)}"


@asynccontextmanager
async def open_gateway(url: str, credential: str, timeout_seconds: float, chat_id: str | None = None) -> AsyncIterator[GatewayServer]:
    """Open one MCP session to the gateway as the user who owns `credential`.

    Lists tools straight away, so a bad credential fails here, before any
    model call is paid for.
    """
    server = GatewayServer(url, credential, timeout_seconds, chat_id)
    try:
        await server.connect()
        await server.list_tools()
    except Exception as exc:
        await server.cleanup()
        log.warning("gateway session failed: %r", exc)
        raise GatewayUnavailable(_describe_connect_error(exc, server.auth)) from exc
    try:
        yield server
    finally:
        await server.cleanup()


def _describe_connect_error(exc: BaseException, auth: _AuthWatch) -> str:
    if auth.rejected:
        return "The gateway did not accept your credential. Sign in again, or ask an admin to check your access."
    cause = _root_cause(exc)
    # The SDK reports a failed connect as UserError and drops the cause.
    if isinstance(cause, (UserError, httpx2.ConnectError, httpx2.TimeoutException, TimeoutError)):
        return "The gateway is not reachable right now."
    if isinstance(cause, MCPError):
        return f"The gateway refused the session: {cause.message}"
    return f"Could not open a session with the gateway ({type(cause).__name__})."


def _root_cause(exc: BaseException) -> BaseException:
    """Dig through task-group wrappers and the SDK's own wrapping."""
    seen: set[int] = set()
    while id(exc) not in seen and not isinstance(exc, MCPError):
        seen.add(id(exc))
        if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            exc = exc.exceptions[0]
        elif exc.__cause__ is not None:
            exc = exc.__cause__
        else:
            break
    return exc
