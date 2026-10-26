"""The dashboard-facing HTTP API: auth header, SSE events, conversation ownership."""

from __future__ import annotations

import json

import httpx2
import pytest

import asyncio

from chat_agent import server
from chat_agent.agent import ChatAgent
from chat_agent.approvals import ApprovalRegistry

from .conftest import FakeModels, ScriptedModel, call, say, settings_for

pytestmark = pytest.mark.anyio


def parse_sse(body: str) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
async def api(gateway):
    server.app.state.agent = ChatAgent(FakeModels(ScriptedModel([[say("Hello.")]])), settings_for(gateway), approvals=ApprovalRegistry())
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=server.app), base_url="http://chat") as client:
        yield client


@pytest.fixture
async def writing_api(gateway):
    """An agent whose model wants to create a product, so the turn stops for approval."""
    replies = [[call("c1", "woo__create_product", name="Training Ball", regular_price="25.00")], [say("Done.")]]
    server.app.state.agent = ChatAgent(FakeModels(ScriptedModel(replies)), settings_for(gateway, approval_timeout_seconds=5), approvals=ApprovalRegistry())
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=server.app), base_url="http://chat") as client:
        yield client


async def wait_for_pending(registry: ApprovalRegistry):
    for _ in range(200):
        if registry.pending():
            return registry.pending()[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no approval request appeared")


async def test_requires_a_bearer_credential(api):
    response = await api.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 401


async def test_streams_events_and_keeps_the_conversation(api):
    first = await api.post("/api/chat", json={"message": "hi"}, headers={"Authorization": "Bearer dev-user-a"})
    events = parse_sse(first.text)
    assert first.headers["content-type"].startswith("text/event-stream")
    assert [e["type"] for e in events] == ["conversation", "tools", "text", "done"]

    conversation_id = events[0]["id"]
    again = await api.post(
        "/api/chat", json={"message": "and again", "conversation_id": conversation_id}, headers={"Authorization": "Bearer dev-user-a"}
    )
    assert parse_sse(again.text)[0] == {"type": "conversation", "id": conversation_id, "model": server.settings.model, "model_label": "Kimi K3"}


async def test_another_user_cannot_continue_my_conversation(api):
    first = await api.post("/api/chat", json={"message": "hi"}, headers={"Authorization": "Bearer dev-user-a"})
    conversation_id = parse_sse(first.text)[0]["id"]

    other = await api.post(
        "/api/chat", json={"message": "hi", "conversation_id": conversation_id}, headers={"Authorization": "Bearer dev-user-b"}
    )
    assert other.status_code == 404


async def test_a_rejected_credential_leaves_nothing_stored(api, gateway):
    gateway.fake.revoked.add("dev-user-b")
    response = await api.post("/api/chat", json={"message": "hi"}, headers={"Authorization": "Bearer dev-user-b"})
    events = parse_sse(response.text)
    assert events[-1]["type"] == "error"
    assert server.store.get_or_create(events[0]["id"], "dev-user-b") is None


async def test_rejects_empty_and_oversized_messages(api):
    headers = {"Authorization": "Bearer dev-user-a"}
    assert (await api.post("/api/chat", json={"message": "  "}, headers=headers)).status_code == 400
    too_long = "x" * (server.settings.max_message_chars + 1)
    assert (await api.post("/api/chat", json={"message": too_long}, headers=headers)).status_code == 400


async def test_meta_names_the_model(api):
    response = await api.get("/api/chat/meta")
    assert response.status_code == 200
    assert response.json() == {"model": server.settings.model, "model_label": "Kimi K3",
                               "approval_timeout_seconds": server.settings.approval_timeout_seconds}


async def test_approve_releases_the_held_call(writing_api, gateway):
    headers = {"Authorization": "Bearer dev-user-a"}
    registry = server.app.state.agent.approvals
    # The response body only completes when the turn ends, so run it as a task.
    turn = asyncio.create_task(writing_api.post("/api/chat", json={"message": "create it"}, headers=headers))
    pending = await wait_for_pending(registry)
    assert gateway.fake.audit == []  # the gateway has not seen the call

    answer = await writing_api.post(
        "/api/chat/approve",
        json={"conversation_id": pending.conversation_id, "request_id": pending.request_id, "decision": "approve"},
        headers=headers,
    )
    assert answer.status_code == 200 and answer.json() == {"ok": True}

    events = parse_sse((await turn).text)
    assert [e["type"] for e in events] == [
        "conversation", "tools", "tool_call", "approval_required", "approval_decided", "tool_result", "text", "done",
    ]
    asked = events[3]
    assert asked["request_id"] == pending.request_id and asked["call_id"] == "c1"
    assert asked["name"] == "woo__create_product" and asked["server"] == "woo" and asked["read_only"] is False
    assert asked["summary"] == "Run woo__create_product on WooCommerce" and asked["server_name"] == "WooCommerce"
    assert asked["input"] == {"name": "Training Ball", "regular_price": "25.00"}
    assert events[4] == {"type": "approval_decided", "request_id": pending.request_id, "decision": "approve"}
    assert not events[5]["is_error"] and "denied" not in events[5]
    assert [r["tool"] for r in gateway.fake.audit] == ["woo__create_product"]
    assert registry.pending() == []


async def test_deny_keeps_the_call_away_from_the_gateway(writing_api, gateway):
    headers = {"Authorization": "Bearer dev-user-a"}
    registry = server.app.state.agent.approvals
    turn = asyncio.create_task(writing_api.post("/api/chat", json={"message": "create it"}, headers=headers))
    pending = await wait_for_pending(registry)

    answer = await writing_api.post(
        "/api/chat/approve",
        json={"conversation_id": pending.conversation_id, "request_id": pending.request_id, "decision": "deny"},
        headers=headers,
    )
    assert answer.status_code == 200

    events = parse_sse((await turn).text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] and result["denied"] is True and result["timed_out"] is False
    assert result["output"] == "The user denied this call. It did not run."
    assert gateway.fake.audit == []


async def test_only_the_owner_can_decide(writing_api, gateway):
    registry = server.app.state.agent.approvals
    turn = asyncio.create_task(writing_api.post("/api/chat", json={"message": "create it"}, headers={"Authorization": "Bearer dev-user-a"}))
    pending = await wait_for_pending(registry)
    body = {"conversation_id": pending.conversation_id, "request_id": pending.request_id, "decision": "approve"}

    assert (await writing_api.post("/api/chat/approve", json=body)).status_code == 401
    other = await writing_api.post("/api/chat/approve", json=body, headers={"Authorization": "Bearer dev-user-b"})
    assert other.status_code == 404
    bad = await writing_api.post("/api/chat/approve", json={"decision": "maybe"}, headers={"Authorization": "Bearer dev-user-a"})
    assert bad.status_code == 400
    assert gateway.fake.audit == []  # still held

    ok = await writing_api.post("/api/chat/approve", json=body, headers={"Authorization": "Bearer dev-user-a"})
    assert ok.status_code == 200
    again = await writing_api.post("/api/chat/approve", json=body, headers={"Authorization": "Bearer dev-user-a"})
    assert again.status_code in (404, 409)  # decided, or already cleaned up after the turn moved on
    await turn
    assert [r["tool"] for r in gateway.fake.audit] == ["woo__create_product"]
