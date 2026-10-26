"""The gateway's single choke point.

Both doors into the gateway end here:

* an external MCP client, authenticated by its API key (mcp_endpoint.py)
* the dashboard chat agent, authenticated by the login session (chat.py)

So both get the same tool list, the same permission checks, the same timeouts
and the same audit trail. Nothing calls an upstream any other way.

Public tool names are `<server slug>__<upstream tool name>`, e.g.
`woo__list_orders`. Slugs cannot contain "__", so the first "__" splits a name
unambiguously, and two upstreams that both offer `list_products` stay distinct.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from urllib.parse import quote

import anyio
import mcp_types as types
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from gateway import upstreams
from gateway.db import AuditEvent, Database, Server, Tool, utcnow
from gateway.security import CredentialBox

log = logging.getLogger("gateway.core")

SEPARATOR = "__"
MAX_AUDIT_ARGS = 2000
ROLES = ("admin", "member")
IDENTITY_HEADER = "X-Urumi-User"


@dataclass(frozen=True)
class Identity:
    """Who is calling. Always derived from a verified credential, never from input."""

    user_id: int
    email: str
    role: Literal["admin", "member"]
    via: Literal["mcp", "chat"]
    api_key_prefix: str | None = None
    chat_id: str | None = None
    name: str = ""
    # "search": tools/list returns the two search-mode tools (toolsearch.py) instead of every tool.
    tool_mode: Literal["all", "search"] = "all"


def public_name(slug: str, tool: str) -> str:
    return f"{slug}{SEPARATOR}{tool}"


def split_name(name: str) -> tuple[str, str] | None:
    slug, sep, tool = name.partition(SEPARATOR)
    return (slug, tool) if sep and slug and tool else None


# Characters a person cannot see but a model reads: Unicode "tag" characters,
# zero-width characters and bidirectional overrides. A tool description is the
# upstream's text going straight into the model's context, so these are stripped
# before any model sees it, and their presence holds the tool for review.
_HIDDEN = re.compile("[\U000E0000-\U000E007F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]")
MAX_DESCRIPTION = 1500


def clean_description(text: str) -> str:
    text = _HIDDEN.sub("", text or "").strip()
    return text if len(text) <= MAX_DESCRIPTION else text[: MAX_DESCRIPTION - 1] + "…"


def has_hidden_characters(text: str | None) -> bool:
    return bool(text and _HIDDEN.search(text))


def definition_of(row: Tool) -> dict[str, Any]:
    """What a model is shown for a tool. Any change to it needs an admin's review."""
    return {"title": row.title, "description": row.description, "input_schema": row.input_schema,
            "output_schema": row.output_schema, "annotations": row.annotations}


def definition_hash(definition: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def pending_review(row: Tool) -> bool:
    return row.definition_hash is not None and row.definition_hash != row.approved_hash


def default_roles(annotations: types.ToolAnnotations | None) -> list[str]:
    """First-discovery default: members get tools that declare themselves read-only.

    The hint comes from the upstream and is not proof of anything. It only sets
    the starting rule; the admin reviews it on the Servers page, and the gateway
    enforces whatever rule is stored.
    """
    if annotations is not None and annotations.read_only_hint:
        return ["admin", "member"]
    return ["admin"]


class Gateway:
    def __init__(self, db: Database, box: CredentialBox):
        self.db = db
        self.box = box
        self._refresh_lock = anyio.Lock()

    # ------------------------------------------------------------------ targets

    def target_for(self, server: Server, who: Identity | None = None) -> upstreams.Target:
        headers: dict[str, str] = {}
        if who is not None and server.forward_identity and who.name.strip():
            # Percent-encoded so any name is a valid header value. The display name only, never the email.
            headers[IDENTITY_HEADER] = quote(" ".join(who.name.split())[:80], safe="")
        if server.auth_type != "none" and server.credential_enc:
            secret = self.box.open(server.credential_enc)
            if secret is not None:
                if server.auth_type == "bearer":
                    headers["Authorization"] = f"Bearer {secret}"
                elif server.auth_type == "header" and server.auth_header:
                    headers[server.auth_header] = secret
        return upstreams.Target(
            slug=server.slug,
            url=server.url,
            timeout=server.timeout_seconds,
            headers=headers,
            protocol_version=server.protocol_version,
        )

    # ---------------------------------------------------------------- discovery

    async def refresh_server(self, server_id: int) -> Server | None:
        """Re-list one upstream's tools and record its health. Never raises for upstream trouble."""
        async with self.db.session() as s:
            server = await s.get(Server, server_id, options=[selectinload(Server.tools)])
            if server is None:
                return None
            target = self.target_for(server)
        # The upstream call happens outside any database transaction.
        try:
            found = await upstreams.discover(target)
        except upstreams.UpstreamFailure as failure:
            found, failure_ = None, failure
        else:
            failure_ = None

        async with self.db.session() as s:
            server = await s.get(Server, server_id, options=[selectinload(Server.tools)])
            if server is None:
                return None
            now = utcnow()
            server.last_checked_at = now
            if failure_ is not None:
                server.status = "auth_failed" if failure_.kind == "auth_failed" else "down"
                server.last_error = failure_.message
            else:
                assert found is not None
                # Trust on first use: what a server offers when an admin first connects it
                # (or what the platform's own store servers offer) counts as reviewed.
                # Anything that changes or appears after that waits for an admin.
                trust_new = server.last_ok_at is None or server.managed_by is not None
                server.status, server.last_error, server.last_ok_at = "ok", None, now
                server.latency_ms, server.protocol_version = found.latency_ms, found.protocol_version
                self._merge_tools(server, found.tools, trust_new)
            await s.commit()
            return server

    @staticmethod
    def _merge_tools(server: Server, discovered: list[types.Tool], trust_new: bool = True) -> None:
        existing = {t.name: t for t in server.tools}
        seen: set[str] = set()
        for tool in discovered:
            if len(public_name(server.slug, tool.name)) > 128:
                log.warning("skipping %s/%s: public name would exceed 128 characters", server.slug, tool.name)
                continue
            seen.add(tool.name)
            row = existing.get(tool.name)
            annotations = tool.annotations.model_dump(by_alias=True, exclude_none=True) if tool.annotations else None
            if row is None:
                row = Tool(name=tool.name, allowed_roles=default_roles(tool.annotations))
                server.tools.append(row)
            legacy = row.id is not None and row.definition_hash is None  # stored before pinning existed
            row.title = tool.title
            row.description = tool.description or ""
            row.input_schema = tool.input_schema
            row.output_schema = tool.output_schema
            row.annotations = annotations
            row.present = True
            definition = definition_of(row)
            row.definition_hash = definition_hash(definition)
            hidden = has_hidden_characters(row.description) or has_hidden_characters(row.title)
            if row.approved_hash is None and (legacy or trust_new) and not hidden:
                row.approved_hash, row.approved_definition = row.definition_hash, definition
            elif row.definition_hash != row.approved_hash:
                log.warning("%s/%s: definition differs from the approved one; held for review", server.slug, tool.name)
        for name, row in existing.items():
            if name not in seen:
                row.present = False

    async def refresh_all(self) -> None:
        """Refresh every enabled upstream concurrently. A slow one only delays itself."""
        async with self._refresh_lock:
            async with self.db.session() as s:
                ids = list(await s.scalars(select(Server.id).where(Server.enabled.is_(True))))
            async with anyio.create_task_group() as tg:
                for server_id in ids:
                    tg.start_soon(self.refresh_server, server_id)

    # ------------------------------------------------------------- tool listing

    async def visible_tools(self, who: Identity) -> list[tuple[Server, Tool]]:
        """Tools this person may use right now: enabled servers, present tools, allowed role.

        Reads the stored catalog, never the upstreams, so a dead upstream cannot
        slow down discovery. A server that is enabled but currently down keeps its
        tools listed; calling one returns a clear "unreachable" error.
        """
        async with self.db.session() as s:
            rows = await s.execute(
                select(Server, Tool)
                .join(Tool, Tool.server_id == Server.id)
                .where(Server.enabled.is_(True), Tool.present.is_(True))
                .order_by(Server.slug, Tool.name)
            )
            return [(server, tool) for server, tool in rows.all() if who.role in tool.allowed_roles and not pending_review(tool)]

    async def list_tools(self, who: Identity) -> list[types.Tool]:
        result = []
        for server, tool in await self.visible_tools(who):
            annotations = types.ToolAnnotations.model_validate(tool.annotations) if tool.annotations else None
            result.append(
                types.Tool(
                    name=public_name(server.slug, tool.name),
                    title=f"{server.name}: {clean_description(tool.title or tool.name)}",
                    description=f"[{server.name}] {clean_description(tool.description)}".strip(),
                    input_schema=tool.input_schema,
                    output_schema=tool.output_schema,
                    annotations=annotations,
                )
            )
        return result

    # ---------------------------------------------------------------- tool call

    async def call_tool(self, who: Identity, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        """Authorize, route, time-limit and audit one tool call."""
        arguments = arguments or {}
        started = time.monotonic()
        event = AuditEvent(
            started_at=utcnow(),
            user_id=who.user_id,
            user_email=who.email,
            via=who.via,
            api_key_prefix=who.api_key_prefix,
            chat_id=who.chat_id,
            public_name=name[:200],
            arguments=_truncate_json(arguments),
        )

        def finish(outcome: str, result: types.CallToolResult, error: str | None = None) -> types.CallToolResult:
            event.outcome = outcome
            event.error = error
            event.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        try:
            result = await self._route(who, name, arguments, event, finish)
        finally:
            if getattr(event, "outcome", None) is None:
                # Cancelled (client went away) or crashed mid-call: record that too.
                event.outcome, event.duration_ms = "aborted", int((time.monotonic() - started) * 1000)
            with anyio.CancelScope(shield=True):
                await self._record(event)
        return result

    async def _route(self, who, name, arguments, event, finish) -> types.CallToolResult:  # noqa: ANN001
        parts = split_name(name)
        if parts is None:
            return finish("unknown_tool", _error(f"Unknown tool {name!r}. Tool names look like 'server__tool'."), "malformed name")
        slug, tool_name = parts
        event.server_slug, event.tool_name = slug[:32], tool_name[:128]

        # Fresh read on every call: a client holding an old tool list cannot use
        # a server that has since been disabled, or a tool it has lost access to.
        async with self.db.session() as s:
            server = await s.scalar(select(Server).where(Server.slug == slug))
            tool = None
            if server is not None:
                tool = await s.scalar(select(Tool).where(Tool.server_id == server.id, Tool.name == tool_name))

        if server is None or tool is None or not tool.present:
            return finish("unknown_tool", _error(f"Unknown tool {name!r}."), "not in catalog")
        if not server.enabled:
            return finish("disabled", _error(f"The {server.name} server is disabled by an admin."), "server disabled")
        if pending_review(tool):
            return finish("pending_review", _error(
                f"{name} is held back: the {server.name} server changed what this tool says it does, or added it, "
                "after an admin last reviewed it. An admin can review and approve it on the Servers page."), "definition not approved")
        if who.role not in tool.allowed_roles:
            return finish("denied", _error(f"Your role ({who.role}) is not allowed to use {name}. An admin can change this on the Permissions page."), "role not allowed")

        read_only = bool((tool.annotations or {}).get("readOnlyHint"))
        try:
            result = await upstreams.call_tool(self.target_for(server, who), tool_name, arguments)
        except upstreams.UpstreamFailure as failure:
            await self._note_health(server.id, failure)
            message = failure.message
            if failure.kind == "timeout" and not read_only:
                message += (
                    " OUTCOME UNKNOWN: this tool can change data and the upstream may have finished the change. "
                    "Check the current state before retrying."
                )
            return finish(failure.kind, _error(message), failure.message)

        await self._note_health(server.id, None)
        if result.is_error:
            result = types.CallToolResult(
                content=[types.TextContent(type="text", text=tidy_error_text(c.text)) if isinstance(c, types.TextContent) else c for c in result.content],
                is_error=True,
                structured_content=result.structured_content,
            )
            text = " ".join(c.text for c in result.content if isinstance(c, types.TextContent))
            return finish("tool_error", result, text[:1000] or "upstream reported an error")
        return finish("ok", result)

    async def _note_health(self, server_id: int, failure: upstreams.UpstreamFailure | None) -> None:
        """Let real calls update the status page, not only the periodic refresh."""
        async with self.db.session() as s:
            server = await s.get(Server, server_id)
            if server is None:
                return
            now = utcnow()
            if failure is None:
                if server.status != "ok" or not server.last_ok_at or now - server.last_ok_at > timedelta(seconds=30):
                    server.status, server.last_error, server.last_ok_at, server.last_checked_at = "ok", None, now, now
                    await s.commit()
            elif failure.kind in ("unreachable", "timeout", "auth_failed"):
                server.status = "auth_failed" if failure.kind == "auth_failed" else "down"
                server.last_error, server.last_checked_at = failure.message, now
                await s.commit()

    async def _record(self, event: AuditEvent) -> None:
        try:
            async with self.db.session() as s:
                s.add(event)
                await s.commit()
        except Exception:  # an audit write failure must be loud, but must not hide the result
            log.exception("failed to write audit event for %s", event.public_name)


_ONE_PROBLEM = re.compile(
    r"(?P<field>[\w.\[\]-]+)\s+(?P<msg>.+?)\s*\[type=\w+, input_value=.*?, input_type=\w+\]\s*For further information visit \S+",
    re.S,
)
_VALIDATION = re.compile(r"(?:Error executing tool \w+:\s*)?\d+ validation errors? for (\w+?)Arguments\s+(?P<rest>.*)", re.S)


def tidy_error_text(text: str) -> str:
    """Say what is wrong with the arguments without the validation library's internals.

    MCP servers built on pydantic report a bad argument with the library's class name
    ("list_ordersArguments"), a type code, the offending value and a link to its docs
    for a specific version. A person or a model needs only the field and the rule.
    """
    m = _VALIDATION.search(text)
    if not m:
        return text
    problems = [f"{p['field']}: {p['msg'].strip()}" for p in _ONE_PROBLEM.finditer(m["rest"])]
    if not problems:
        return text
    return f"Invalid arguments for {m.group(1)}. " + "; ".join(problems) + "."


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


def _truncate_json(value: Any) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= MAX_AUDIT_ARGS else text[: MAX_AUDIT_ARGS - 3] + "..."
