"""A stand-in for the real gateway, for tests and local demos.

It speaks real MCP over Streamable HTTP and checks a bearer token on every
request, like the real gateway will. Behind it are fake upstreams (`woo__`,
`stock__`, one dotted `crm.` name, and a slow tool). It records an audit row
per call, so tests can check which user each call ran as.

Run it on its own for the CLI demo:

    uv run python -m tests.fake_gateway        # serves http://127.0.0.1:8100/mcp
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import mcp_types as types
from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError

TOKENS = {"dev-user-a": "user-a", "dev-user-b": "user-b"}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


# The real gateway titles tools "<Server name>: <tool title>" and passes the
# upstream's annotations through. A tool without read_only_hint can change
# data, so chat stops it for approval.
READ_ONLY = types.ToolAnnotations(read_only_hint=True)
WRITES = types.ToolAnnotations(read_only_hint=False)

TOOLS = [
    types.Tool(
        name="woo__list_products",
        title="WooCommerce: List products",
        description="List store products, optionally filtered by a search term.",
        input_schema=_schema({"search": {"type": "string"}}, []),
        annotations=READ_ONLY,
    ),
    types.Tool(
        name="woo__create_product",
        title="WooCommerce: Create product",
        description="Create a simple product. regular_price is a decimal string such as '25.00'.",
        input_schema=_schema({"name": {"type": "string"}, "regular_price": {"type": "string"}}, ["name", "regular_price"]),
        annotations=WRITES,
    ),
    types.Tool(
        name="woo__list_orders",
        title="WooCommerce: List orders",
        description="List store orders. period is one of today, yesterday, last_7_days.",
        input_schema=_schema({"period": {"type": "string", "enum": ["today", "yesterday", "last_7_days"]}}, ["period"]),
        annotations=READ_ONLY,
    ),
    types.Tool(
        name="stock__get_stock",
        title="Stock (demo): Get stock",
        description="Warehouse stock level for a product, by product name. Demo data.",
        input_schema=_schema({"product_name": {"type": "string"}}, ["product_name"]),
        annotations=READ_ONLY,
    ),
    types.Tool(
        name="crm.lookup_customer",
        description="Look up a customer by email. Demo data.",
        input_schema=_schema({"email": {"type": "string"}}, ["email"]),
        annotations=READ_ONLY,
    ),
    types.Tool(
        name="slow__wait",
        description="Waits for the given number of seconds, then answers. For timeout tests.",
        input_schema=_schema({"seconds": {"type": "number"}}, ["seconds"]),
        annotations=READ_ONLY,
    ),
]


@dataclass
class FakeGateway:
    disabled: set[str] = field(default_factory=set)  # server prefixes, e.g. {"stock"}
    revoked: set[str] = field(default_factory=set)
    audit: list[dict[str, Any]] = field(default_factory=list)
    products: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": 11, "name": "Football Size 5", "regular_price": "30.00"},
            {"id": 12, "name": "Shin Guards", "regular_price": "15.00"},
        ]
    )

    def __post_init__(self) -> None:
        self.server = Server("fake-gateway", on_list_tools=self._list_tools, on_call_tool=self._call_tool)

    def asgi_app(self):
        inner = self.server.streamable_http_app(
            streamable_http_path="/mcp",
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

        async def app(scope, receive, send):
            if scope["type"] == "http":
                headers = dict(scope["headers"])
                scheme, _, token = headers.get(b"authorization", b"").decode().partition(" ")
                user = TOKENS.get(token) if scheme.lower() == "bearer" and token not in self.revoked else None
                if user is None:
                    # The same answer the real gateway gives: HTTP 401 with a
                    # JSON-RPC error whose code is not one the MCP SDK uses itself.
                    await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": b'{"jsonrpc":"2.0","id":null,"error":{"code":-32030,"message":"Invalid or revoked API key"}}'})
                    return
                scope.setdefault("state", {})["user"] = user
            await inner(scope, receive, send)

        return app

    def _user(self, ctx) -> str:
        request = ctx.request
        return getattr(request.state, "user", "in-process") if request is not None else "in-process"

    def _enabled(self, name: str) -> bool:
        return name.replace(".", "__").split("__", 1)[0] not in self.disabled

    async def _list_tools(self, ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[t for t in TOOLS if self._enabled(t.name)])

    async def _call_tool(self, ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        user, args = self._user(ctx), params.arguments or {}
        row = {"user": user, "tool": params.name, "arguments": args}
        self.audit.append(row)
        if not any(t.name == params.name for t in TOOLS) or not self._enabled(params.name):
            # The real gateway's answer for a tool the caller cannot use: a JSON-RPC
            # error, the same for a tool that does not exist and one that is not theirs.
            row["outcome"] = "rejected"
            raise MCPError(code=-32602, message=f"Unknown tool: {params.name}")
        result = await self._run(params.name, args)
        row["outcome"] = "error" if result.get("error") else "ok"
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result))],
            is_error=bool(result.get("error")),
        )

    async def _run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        today = datetime.now(UTC).date().isoformat()
        if name == "woo__list_products":
            term = str(args.get("search", "")).lower()
            return {"products": [p for p in self.products if term in p["name"].lower()]}
        if name == "woo__create_product":
            product = {"id": 10 + len(self.products) + 1, "name": args["name"], "regular_price": args["regular_price"]}
            self.products.append(product)
            return {"created": product}
        if name == "woo__list_orders":
            if args.get("period") != "today":
                return {"orders": []}
            return {
                "orders": [
                    {"id": 1042, "date": today, "status": "processing", "total": "30.00", "items": [{"name": "Football Size 5", "quantity": 1}]},
                    {"id": 1043, "date": today, "status": "processing", "total": "45.00", "items": [{"name": "Shin Guards", "quantity": 3}]},
                ]
            }
        if name == "stock__get_stock":
            levels = {"football size 5": 40, "shin guards": 2}
            level = levels.get(str(args["product_name"]).lower())
            return {"product_name": args["product_name"], "in_stock": level} if level is not None else {"error": "unknown product"}
        if name == "crm.lookup_customer":
            return {"email": args["email"], "name": "Sam Rivera", "orders": 4}
        if name == "slow__wait":
            await asyncio.sleep(float(args["seconds"]))
            return {"waited": args["seconds"]}
        return {"error": "not implemented"}


if __name__ == "__main__":
    import uvicorn

    print("Fake gateway on http://127.0.0.1:8100/mcp  tokens:", ", ".join(TOKENS))
    uvicorn.run(FakeGateway().asgi_app(), host="127.0.0.1", port=8100, log_level="warning")
