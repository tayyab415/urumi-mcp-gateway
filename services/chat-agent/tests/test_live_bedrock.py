"""The real model on Bedrock, driving the fake gateway. Costs a fraction of a cent.

    RUN_LIVE_BEDROCK=1 uv run pytest -m live -s
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from chat_agent.agent import ChatAgent
from chat_agent.model import BedrockModels

from .conftest import settings_for

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_BEDROCK") != "1", reason="set RUN_LIVE_BEDROCK=1 to call Bedrock"),
]


async def turn(agent: ChatAgent, history: list[Any], message: str, token: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    await agent.run_turn(history, message, token, events.append)
    reply = "".join(e["delta"] for e in events if e["type"] == "text")
    calls = [(e["name"], e["input"]) for e in events if e["type"] == "tool_call"]
    print(f"\n> {message}\n  tools called: {calls}\n  reply: {reply.strip()}\n  end: {events[-1]}")
    return events


async def test_brief_chat_scenarios(gateway):
    settings = settings_for(gateway)
    models = BedrockModels(settings)
    agent = ChatAgent(models, settings)
    try:
        # User A creates a product through chat.
        history_a: list[Any] = []
        await turn(agent, history_a, "Create a product called Training Ball for 25.", "dev-user-a")
        created = [r for r in gateway.fake.audit if r["tool"] == "woo__create_product"]
        assert len(created) == 1 and created[0]["user"] == "user-a"
        assert created[0]["arguments"]["name"] == "Training Ball"
        assert float(created[0]["arguments"]["regular_price"]) == 25

        # Same conversation: what came in today.
        events = await turn(agent, history_a, "What orders came in today?", "dev-user-a")
        assert any(r["tool"] == "woo__list_orders" and r["user"] == "user-a" for r in gateway.fake.audit)
        assert events[-1]["type"] == "done"

        # User B, one prompt that needs two upstream servers.
        history_b: list[Any] = []
        await turn(agent, history_b, "Of the products in today's orders, which are low on warehouse stock?", "dev-user-b")
        b_tools = {r["tool"] for r in gateway.fake.audit if r["user"] == "user-b"}
        assert {"woo__list_orders", "stock__get_stock"} <= b_tools

        # Admin disables the stock server. Its tools are gone on B's next message.
        gateway.fake.disabled.add("stock")
        events = await turn(agent, history_b, "Check Shin Guards stock again.", "dev-user-b")
        assert not any(name.startswith("stock__") for name in events[0]["tools"])
        assert events[-1]["type"] == "done"

        # Admin revokes B's key. The next message is refused before any model call.
        gateway.fake.revoked.add("dev-user-b")
        events = await turn(agent, history_b, "And today's orders?", "dev-user-b")
        assert events == [{"type": "error", "message": events[0]["message"]}]
        assert "credential" in events[0]["message"]
    finally:
        await models.close()
