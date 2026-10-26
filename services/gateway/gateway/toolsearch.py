"""Search mode: two tools instead of hundreds.

Ten upstreams can mean hundreds of tools, and every tool description sits in
the model's context on every turn. In search mode a client sees only:

    gateway__search_tools(query, limit)    find tools by what they do
    gateway__call_tool(name, arguments)    call one of them

A client opts in per connection: `<gateway>/mcp?tools=search`, or the header
`X-Urumi-Tools: search`. Without it, tools/list returns every tool as before.

Nothing about access changes. Search only returns tools the caller could see
in a normal tools/list, and gateway__call_tool goes through the same
Gateway.call_tool as a direct call: the same role check, disabled-server
check, pinning check, timeout and audit row, under the real tool's name.

Every tools/call here writes one audit row, marked `via = mcp-search` (or
`chat-search`). A search is a row of its own. A gateway__call_tool request is
one row under the tool it called, so the wrapper and the call it made are one
event, counted once. A wrapper refused before any tool is chosen (no name,
itself as the target) gets a row under gateway__call_tool.

Ranking is BM25 over the tool's name, title, description and server name. The
corpus is small (one person's visible tools), so it is rebuilt per search from
the stored catalog; no index to keep in sync with discovery.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import replace
from typing import Any

import mcp_types as types

from gateway.core import Finish, Gateway, Identity, clean_description, public_name

SEARCH = "gateway__search_tools"
CALL = "gateway__call_tool"
META_TOOLS = (SEARCH, CALL)
DEFAULT_LIMIT = 5
MAX_LIMIT = 20

_WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    words = _WORD.findall(text.lower())
    # A crude stem, so "orders" finds "order" and "products" finds "product".
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]


def rank(query: str, docs: list[tuple[str, str]], limit: int) -> list[tuple[str, float]]:
    """BM25 (k1=1.2, b=0.75). docs are (id, text); returns (id, score), best first, scores > 0 only."""
    q = tokens(query)
    if not q or not docs:
        return []
    bags = [(doc_id, Counter(tokens(text))) for doc_id, text in docs]
    avg = sum(sum(b.values()) for _, b in bags) / len(bags) or 1.0
    df = Counter(t for _, b in bags for t in set(b))
    n = len(bags)
    scored = []
    for doc_id, bag in bags:
        length = sum(bag.values())
        score = 0.0
        for term in set(q):
            f = bag.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * length / avg))
        if score > 0:
            scored.append((doc_id, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


async def meta_tools(gateway: Gateway, who: Identity) -> list[types.Tool]:
    visible = await gateway.visible_tools(who)
    servers = Counter(server.name for server, _ in visible)
    catalog = "; ".join(f"{name} ({count} tools)" for name, count in sorted(servers.items())) or "none"
    return [
        types.Tool(
            name=SEARCH,
            title="Search the gateway's tools",
            description=(
                f"Find tools by describing the task, e.g. 'list today's orders' or 'restock quote'. "
                f"Returns matching tool names with their input schemas; then call one with {CALL}. "
                f"You can use {len(visible)} tools from: {catalog}."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What you want to do, in a few words"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT},
                },
                "required": ["query"],
            },
            annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
        ),
        types.Tool(
            name=CALL,
            title="Call a tool found with search",
            description=(
                f"Call a tool by the exact name {SEARCH} returned, with arguments matching its input schema. "
                "The same permissions and audit apply as calling it directly."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact tool name, e.g. woo__list_orders"},
                    "arguments": {"type": "object", "description": "Arguments for that tool", "default": {}},
                },
                "required": ["name"],
            },
            # It can reach write tools, so it does not claim to be read-only.
            annotations=types.ToolAnnotations(read_only_hint=False, open_world_hint=True),
        ),
    ]


def _through_search(who: Identity) -> Identity:
    return replace(who, via=f"{who.via}-search")  # type: ignore[arg-type]


async def search(gateway: Gateway, who: Identity, arguments: dict[str, Any]) -> types.CallToolResult:
    async def route(event: Any, finish: Finish) -> types.CallToolResult:
        query = str(arguments.get("query") or "").strip()
        try:
            limit = max(1, min(MAX_LIMIT, int(arguments.get("limit") or DEFAULT_LIMIT)))
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        if not query:
            return finish("invalid_arguments", _error("Give a query: a few words about what you want to do."), "no query")
        visible = {public_name(server.slug, tool.name): (server, tool) for server, tool in await gateway.visible_tools(who)}
        docs = [
            (name, f"{name.replace('__', ' ').replace('_', ' ').replace('-', ' ')} {server.name} {tool.title or ''} {tool.description}")
            for name, (server, tool) in visible.items()
        ]
        hits = []
        for name, score in rank(query, docs, limit):
            server, tool = visible[name]
            hits.append({
                "name": name,
                "server": server.name,
                "description": clean_description(tool.description),
                "read_only": bool((tool.annotations or {}).get("readOnlyHint")),
                "input_schema": tool.input_schema,
                "score": round(score, 2),
            })
        payload = {"query": query, "matches": hits, "searched": len(visible)}
        text = json.dumps(payload, ensure_ascii=False)
        if not hits:
            text = f"No tools match {query!r} among the {len(visible)} you can use. Try other words."
        return finish("ok", types.CallToolResult(content=[types.TextContent(type="text", text=text)], structured_content=payload))

    return await gateway.audited(_through_search(who), SEARCH, arguments, route)


async def call(gateway: Gateway, who: Identity, arguments: dict[str, Any]) -> types.CallToolResult:
    name = str(arguments.get("name") or "")
    inner = arguments.get("arguments") or {}
    problem = None
    if not name:
        problem = f"Give the tool's name, exactly as {SEARCH} returned it."
    elif name in META_TOOLS:
        problem = f"{CALL} calls upstream tools, not itself or {SEARCH}."
    elif not isinstance(inner, dict):
        problem = "'arguments' must be an object."
    if problem is not None:
        # Refused before any tool was chosen, so the row is the wrapper's own.
        async def refuse(event: Any, finish: Finish) -> types.CallToolResult:
            return finish("invalid_arguments", _error(problem), problem)

        return await gateway.audited(_through_search(who), CALL, arguments, refuse)
    # One row, under the real tool's name and marked as reached through search.
    return await gateway.call_tool(_through_search(who), name, inner)


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)
