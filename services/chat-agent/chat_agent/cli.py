"""Chat with the agent in a terminal. Same loop the dashboard uses.

    uv run python -m chat_agent.cli --token dev-user-a
    uv run python -m chat_agent.cli --gateway http://gateway.local/mcp --token <your key>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import replace
from typing import Any

from .agent import ChatAgent
from .config import Settings
from .model import BedrockModels

DIM, BOLD, RED, RESET = "\033[2m", "\033[1m", "\033[31m", "\033[0m"


def show(event: dict[str, Any]) -> None:
    kind = event["type"]
    if kind == "tools":
        print(f"{DIM}tools: {', '.join(event['tools']) or '(none)'}{RESET}")
    elif kind == "text":
        print(event["delta"], end="", flush=True)
    elif kind == "tool_call":
        print(f"\n{BOLD}→ {event['name']}{RESET} {json.dumps(event['input'])}")
    elif kind == "tool_result":
        mark = f"{RED}error{RESET}" if event["is_error"] else "ok"
        print(f"{DIM}← {mark}{DIM} {event['duration_ms']} ms  {event['output'][:300]}{RESET}")
    elif kind == "done":
        u = event["usage"]
        print(f"\n{DIM}[{event['steps']} model calls, {event['tool_calls']} tool calls; tokens: "
              f"{u['input_tokens']} in ({u['cached_input_tokens']} cached), {u['output_tokens']} out]{RESET}")
    elif kind == "error":
        print(f"\n{RED}! {event['message']}{RESET}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gateway", help="gateway MCP URL (default: $GATEWAY_MCP_URL or the local fake gateway)")
    parser.add_argument("--token", default=os.environ.get("GATEWAY_TOKEN"), help="your gateway credential")
    args = parser.parse_args()
    if not args.token:
        parser.error("pass --token or set GATEWAY_TOKEN")

    settings = Settings.from_env()
    if args.gateway:
        settings = replace(settings, gateway_url=args.gateway)
    models = BedrockModels(settings)
    agent = ChatAgent(models, settings)
    history: list[Any] = []
    print(f"{DIM}model {settings.model} via {settings.gateway_url}. Ctrl-D to quit.{RESET}")
    try:
        while True:
            try:
                text = await asyncio.to_thread(input, "\nyou> ")
            except EOFError:
                break
            if text.strip():
                await agent.run_turn(history, text.strip(), args.token, show)
    finally:
        await models.close()


if __name__ == "__main__":
    asyncio.run(main())
