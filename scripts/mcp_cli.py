"""Use the gateway as an external MCP client would (Claude Code, Cursor), as one seeded team member.

    cd services/gateway && uv run python ../../scripts/mcp_cli.py --as bob tools [prefix]
    ... --as bob call woo__list_orders '{"period": "today"}'
    ... --as ana call slack__post_message '{"text": "[test] hello"}'
    ... --gateway http://127.0.0.1:18081/mcp --host app.urumi.test:18081 --creds ~/.urumi/vm-team-credentials.json --as bob tools

Prints what the client sees: the tool names the person's role allows, or a tool's result or error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

CREDS = Path.home() / ".urumi" / "team-credentials.json"


async def run(args: argparse.Namespace) -> int:
    creds = json.loads(args.creds.read_text())
    email = args.who if "@" in args.who else f"{args.who}@urumi.test"
    headers = {"Authorization": f"Bearer {creds[email]['api_key']}", **({"Host": args.host} if args.host else {})}
    async with httpx2.AsyncClient(headers=headers, timeout=120) as http:
        async with Client(streamable_http_client(args.gateway, http_client=http), cache=None) as client:
            if args.cmd == "tools":
                tools = (await client.list_tools()).tools
                names = sorted(t.name for t in tools if t.name.startswith(args.rest[0] if args.rest else ""))
                print(f"[{email}] {len(names)} tools: {', '.join(names)}")
                return 0
            name, payload = args.rest[0], json.loads(args.rest[1]) if len(args.rest) > 1 else {}
            result = await client.call_tool(name, payload)
            text = " ".join(c.text for c in result.content if hasattr(c, "text"))
            print(f"[{email}] {name} -> {'ERROR' if result.is_error else 'ok'}: {' '.join(text.split())[:1200]}")
            return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--as", dest="who", required=True)
    p.add_argument("--gateway", default="http://localhost:8080/mcp")
    p.add_argument("--host", help="Host header to send, for the cluster ingress through a tunnel (e.g. app.urumi.test:18081)")
    p.add_argument("--creds", type=Path, default=CREDS)
    p.add_argument("cmd", choices=["tools", "call"])
    p.add_argument("rest", nargs="*")
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
