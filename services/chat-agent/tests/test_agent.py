"""The agent turn, against the fake gateway over HTTP and a scripted model."""

from __future__ import annotations

from typing import Any

import pytest

import asyncio

from chat_agent.agent import ChatAgent, model_label
from chat_agent.approvals import ApprovalRegistry

from .conftest import FakeModels, ScriptedModel, call, say, settings_for

pytestmark = pytest.mark.anyio


def make(gateway, replies: list[list[Any]], **settings: Any) -> tuple[ChatAgent, ScriptedModel]:
    model = ScriptedModel(replies)
    return ChatAgent(FakeModels(model), settings_for(gateway, **settings)), model


async def run(agent: ChatAgent, history: list[Any], message: str, token: str = "dev-user-a") -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    await agent.run_turn(history, message, token, events.append)
    return events


def outputs(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in request["input"] if isinstance(i, dict) and i.get("type") == "function_call_output"]


async def test_tool_call_runs_through_gateway_as_the_user(gateway):
    agent, model = make(
        gateway,
        [[call("t1", "woo__create_product", name="Training Ball", regular_price="25.00")], [say("Created Training Ball (ID 13).")]],
    )
    events = await run(agent, [], "Create a product called Training Ball for 25")

    assert [e["type"] for e in events] == ["tools", "tool_call", "tool_result", "text", "done"]
    assert not events[2]["is_error"]
    assert gateway.fake.audit == [
        {"user": "user-a", "tool": "woo__create_product", "arguments": {"name": "Training Ball", "regular_price": "25.00"}, "outcome": "ok"}
    ]
    # The second model call carries the real tool result, matched by call id.
    [result] = outputs(model.requests[1])
    assert result["call_id"] == "t1" and "Training Ball" in str(result["output"])


async def test_text_before_and_after_a_tool_call_is_not_run_together(gateway):
    agent, _ = make(gateway, [[say("I'll check the orders first."), call("t1", "woo__list_orders", period="today")], [say("There are none today.")]])
    events = await run(agent, [], "Any orders today?")

    assert "".join(e["delta"] for e in events if e["type"] == "text") == "I'll check the orders first.\n\nThere are none today."


async def test_parallel_calls_all_reach_the_gateway(gateway):
    agent, model = make(
        gateway,
        [[call("a", "woo__list_orders", period="today"), call("b", "stock__get_stock", product_name="Shin Guards")], [say("Shin Guards are low.")]],
    )
    await run(agent, [], "What is low on stock from today's orders?")

    assert [r["call_id"] for r in outputs(model.requests[1])] == ["a", "b"]
    assert {r["tool"] for r in gateway.fake.audit} == {"woo__list_orders", "stock__get_stock"}


async def test_disabled_server_drops_out_on_the_next_turn(gateway):
    agent, model = make(gateway, [[say("ok")]])
    history: list[Any] = []

    first = await run(agent, history, "hi")
    gateway.fake.disabled.add("stock")
    second = await run(agent, history, "hi again")

    assert "stock__get_stock" in first[0]["tools"]
    assert "stock__get_stock" not in second[0]["tools"]
    assert "stock__get_stock" not in model.requests[-1]["tools"]


async def test_history_carries_over_between_turns(gateway):
    agent, model = make(gateway, [[say("ok")]])
    history: list[Any] = []
    await run(agent, history, "first")
    await run(agent, history, "second")

    texts = [i["content"] for i in model.requests[-1]["input"] if isinstance(i, dict) and i.get("role") == "user"]
    assert len(texts) == 2 and texts[0].endswith("first") and texts[1].endswith("second")
    assert model.requests[0]["instructions"] == model.requests[1]["instructions"]


async def test_rejected_credential_stops_before_any_model_call(gateway):
    gateway.fake.revoked.add("dev-user-b")
    agent, model = make(gateway, [[say("unreachable")]])
    history: list[Any] = []
    events = await run(agent, history, "hi", token="dev-user-b")

    assert events == [{"type": "error", "message": events[0]["message"]}]
    assert "credential" in events[0]["message"]
    assert model.requests == [] and history == []


async def test_disabled_tool_called_anyway_is_refused_not_run(gateway):
    # The model still asks for a tool the gateway has since disabled. It is not in
    # this turn's list, so the call never leaves chat, and the model is told why
    # instead of the turn ending on an error.
    agent, model = make(gateway, [[call("s", "stock__get_stock", product_name="Shin Guards")], [say("Can't check.")]])
    gateway.fake.disabled.add("stock")
    events = await run(agent, [], "stock?")

    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] and "did not run" in result["output"]
    assert events[-1]["type"] == "done"
    [output] = outputs(model.requests[1])
    assert "did not run" in str(output["output"])
    assert gateway.fake.audit == []


async def test_tool_the_gateway_refuses_as_unknown_goes_back_to_the_model(gateway):
    # The tool was in this turn's list, so chat sends the call, but it is gone by then.
    # The gateway answers with a JSON-RPC error (-32602, "Unknown tool"), not a tool
    # result; the model hears that the call did not run, and the turn carries on.
    agent, model = make(gateway, [[call("s", "stock__get_stock", product_name="Shin Guards")], [say("Can't check.")]])
    agent._connect = _disable_after_connect(agent._connect, gateway, "stock")
    events = await run(agent, [], "stock?")

    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"]
    assert result["output"] == "The gateway rejected the call: Unknown tool: stock__get_stock. It did not run."
    assert events[-1]["type"] == "done"
    [output] = outputs(model.requests[1])
    assert "did not run" in str(output["output"])
    assert gateway.fake.audit == [{"user": "user-a", "tool": "stock__get_stock", "arguments": {"product_name": "Shin Guards"}, "outcome": "rejected"}]


async def test_made_up_tool_name_goes_back_to_the_model(gateway):
    agent, model = make(gateway, [[call("u", "woo__delete_everything")], [say("I can't do that.")]])
    events = await run(agent, [], "delete everything")

    kinds = [e["type"] for e in events if e["type"] in ("tool_call", "tool_result", "done", "error")]
    assert kinds == ["tool_call", "tool_result", "done"]
    [output] = outputs(model.requests[1])
    assert "no tool named woo__delete_everything" in str(output["output"])


async def test_timeout_is_reported_as_unknown_outcome(gateway):
    agent, _ = make(gateway, [[call("s", "slow__wait", seconds=3)], [say("It timed out.")]], tool_timeout_seconds=1)
    events = await run(agent, [], "wait")

    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] and "outcome is unknown" in result["output"]


async def test_key_revoked_mid_turn_is_reported_as_did_not_run(gateway):
    agent, _ = make(gateway, [[call("x", "woo__list_orders", period="today")], [say("Refused.")]])
    # Revoke right after the session opens, so the refusal lands on the call itself.
    agent._connect = _revoke_after_connect(agent._connect, gateway)
    events = await run(agent, [], "orders?")

    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] and "did not run" in result["output"]


async def test_loop_stops_after_max_steps_with_valid_history(gateway):
    agent, _ = make(gateway, [[call("x", "woo__list_products")]], max_steps=3)
    history: list[Any] = []
    events = await run(agent, history, "loop forever")

    assert events[-1] == {"type": "error", "message": "Stopped after 3 model calls without a final answer."}
    calls = {i["call_id"] for i in history if isinstance(i, dict) and i.get("type") == "function_call"}
    answered = {i["call_id"] for i in history if isinstance(i, dict) and i.get("type") == "function_call_output"}
    assert calls <= answered


def _revoke_after_connect(connect, gateway):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def wrapped(url, credential, timeout, *rest):
        async with connect(url, credential, timeout, *rest) as server:
            gateway.fake.revoked.add(credential)
            yield server

    return wrapped


def _disable_after_connect(connect, gateway, prefix: str):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def wrapped(url, credential, timeout, *rest):
        async with connect(url, credential, timeout, *rest) as server:  # this lists the tools
            gateway.fake.disabled.add(prefix)
            yield server

    return wrapped


# ----------------------------------------------------------------- approvals


def make_gated(gateway, replies: list[list[Any]], **settings: Any) -> tuple[ChatAgent, ApprovalRegistry]:
    registry = ApprovalRegistry()
    return ChatAgent(FakeModels(ScriptedModel(replies)), settings_for(gateway, **settings), approvals=registry), registry


async def run_gated(agent: ChatAgent, message: str) -> tuple[asyncio.Task, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    task = asyncio.create_task(agent.run_turn([], message, "dev-user-a", events.append, conversation_id="conv-1", owner="owner-a"))
    return task, events


async def pending_request(registry: ApprovalRegistry):
    for _ in range(200):
        if registry.pending("conv-1"):
            return registry.pending("conv-1")[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no approval request appeared")


async def test_write_tool_waits_for_approval_before_the_gateway_sees_it(gateway):
    agent, registry = make_gated(gateway, [[call("w", "woo__create_product", name="Ball", regular_price="9.00")], [say("Created.")]])
    task, events = await run_gated(agent, "create")
    pending = await pending_request(registry)

    # Held: the dashboard knows, the gateway does not.
    assert [e["type"] for e in events] == ["tools", "tool_call", "approval_required"]
    assert gateway.fake.audit == []

    assert registry.decide(pending.request_id, "conv-1", "owner-a", "approve") == "ok"
    await task
    assert [e["type"] for e in events] == ["tools", "tool_call", "approval_required", "approval_decided", "tool_result", "text", "done"]
    assert events[1] == {"type": "tool_call", "id": "w", "name": "woo__create_product", "input": {"name": "Ball", "regular_price": "9.00"}}
    assert gateway.fake.audit[0]["tool"] == "woo__create_product" and gateway.fake.audit[0]["outcome"] == "ok"


async def test_approval_names_the_whole_store(gateway):
    # "Store: North" has a colon of its own; the card must not shorten it to "Store".
    agent, registry = make_gated(gateway, [[call("w", "store-north__create_product", name="Ball", regular_price="9.00")], [say("Created.")]])
    task, events = await run_gated(agent, "create a ball in North")
    pending = await pending_request(registry)
    asked = next(e for e in events if e["type"] == "approval_required")
    assert asked["server"] == "store-north" and asked["server_name"] == "Store: North"
    assert asked["summary"] == "Run store-north__create_product on Store: North"
    registry.decide(pending.request_id, "conv-1", "owner-a", "deny")
    await task


def test_server_name_comes_from_meta_and_otherwise_keeps_the_store_prefix():
    import mcp_types as types

    from chat_agent.gateway import _server_name

    tagged = types.Tool(name="store-north__x", title="Orders: list them", input_schema={"type": "object"},
                        meta={"urumi.io/server": {"slug": "store-north", "name": "Store: North"}})
    assert _server_name(tagged.name, tagged) == "Store: North"  # the data wins over the title
    untagged = types.Tool(name="store-north__create_product", title="Store: North: Create product", input_schema={"type": "object"})
    assert _server_name(untagged.name, untagged) == "Store: North"
    assert _server_name("woo__list_orders", None) == "woo"


async def test_denied_call_goes_back_to_the_model_as_not_run(gateway):
    agent, registry = make_gated(gateway, [[call("w", "woo__create_product", name="Ball", regular_price="9.00")], [say("Not done.")]])
    task, events = await run_gated(agent, "create")
    pending = await pending_request(registry)
    registry.decide(pending.request_id, "conv-1", "owner-a", "deny")
    await task

    result = next(e for e in events if e["type"] == "tool_result")
    assert result["denied"] is True and result["timed_out"] is False and result["is_error"]
    assert result["output"] == "The user denied this call. It did not run."
    assert gateway.fake.audit == []
    model = agent._models.scripted  # type: ignore[attr-defined]
    [output] = outputs(model.requests[1])
    assert "did not run" in str(output["output"])


async def test_no_decision_in_time_counts_as_denied(gateway):
    agent, _ = make_gated(gateway, [[call("w", "woo__create_product", name="Ball", regular_price="9.00")], [say("ok")]], approval_timeout_seconds=0.2)
    task, events = await run_gated(agent, "create")
    await task

    decided = next(e for e in events if e["type"] == "approval_decided")
    result = next(e for e in events if e["type"] == "tool_result")
    assert decided["decision"] == "timeout"
    assert result["denied"] is True and result["timed_out"] is True
    assert result["output"] == "No decision was made in time. The call did not run."
    assert gateway.fake.audit == []


async def test_read_only_tools_never_wait(gateway):
    agent, registry = make_gated(gateway, [[call("r", "woo__list_orders", period="today")], [say("Two orders.")]])
    task, events = await run_gated(agent, "orders?")
    await task
    assert [e["type"] for e in events] == ["tools", "tool_call", "tool_result", "text", "done"]
    assert registry.pending() == []


async def test_wrong_owner_or_conversation_cannot_decide(gateway):
    agent, registry = make_gated(gateway, [[call("w", "woo__create_product", name="Ball", regular_price="9.00")], [say("ok")]])
    task, _ = await run_gated(agent, "create")
    pending = await pending_request(registry)
    assert registry.decide(pending.request_id, "conv-1", "owner-b", "approve") == "not_found"
    assert registry.decide(pending.request_id, "conv-2", "owner-a", "approve") == "not_found"
    assert registry.decide("nope", "conv-1", "owner-a", "approve") == "not_found"
    assert gateway.fake.audit == []
    assert registry.decide(pending.request_id, "conv-1", "owner-a", "deny") == "ok"
    assert registry.decide(pending.request_id, "conv-1", "owner-a", "deny") in ("already_decided", "not_found")
    await task


def test_model_labels():
    assert model_label("us.moonshotai.kimi-k3") == "Kimi K3"
    assert model_label("us.openai.gpt-6-luna") == "GPT-6 Luna"
    assert model_label("claude-haiku-5-5") == "Claude Haiku 5.5"
    assert model_label("anthropic.claude-sonnet-5-5-v1:0") == "Claude Sonnet 5.5"
    assert model_label("some-other-model") == "some-other-model"
