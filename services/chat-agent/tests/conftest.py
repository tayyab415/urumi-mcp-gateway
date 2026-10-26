from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import uvicorn
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from chat_agent.config import Settings

from .fake_gateway import FakeGateway


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class RunningGateway:
    fake: FakeGateway
    url: str


@pytest.fixture
async def gateway() -> AsyncIterator[RunningGateway]:
    """The fake gateway, served over real HTTP on a free local port."""
    fake = FakeGateway()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(fake.asgi_app(), log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.02)
    yield RunningGateway(fake, f"http://127.0.0.1:{sock.getsockname()[1]}/mcp")
    server.should_exit = True
    await task


def settings_for(gateway: RunningGateway, **overrides: Any) -> Settings:
    return Settings(gateway_url=gateway.url, **overrides)


def call(call_id: str, tool: str, /, **arguments: Any) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        type="function_call", id=f"fc_{call_id}", call_id=call_id, name=tool, arguments=json.dumps(arguments), status="completed"
    )


def say(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        type="message",
        id="msg_1",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class ScriptedModel(Model):
    """Stands in for the real model. Each model call returns the next scripted reply.

    The last reply repeats once the script runs out. `requests` keeps what
    the SDK sent on each call, so tests can check it.
    """

    def __init__(self, replies: list[list[Any]]):
        self._replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def _next(self, system_instructions: str | None, input: Any, tools: list[Any]) -> list[Any]:
        self.requests.append({"instructions": system_instructions, "input": list(input), "tools": [t.name for t in tools]})
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]

    async def get_response(self, system_instructions, input, model_settings, tools, *args: Any, **kwargs: Any) -> ModelResponse:
        output = self._next(system_instructions, input, tools)
        return ModelResponse(output=output, usage=Usage(requests=1, input_tokens=10, output_tokens=5), response_id="resp_1")

    async def stream_response(self, system_instructions, input, model_settings, tools, *args: Any, **kwargs: Any):
        output = self._next(system_instructions, input, tools)
        seq = 0
        for item in output:
            if isinstance(item, ResponseOutputMessage):
                for part in item.content:
                    yield ResponseTextDeltaEvent.model_construct(
                        type="response.output_text.delta", delta=part.text, item_id=item.id,
                        output_index=0, content_index=0, sequence_number=seq, logprobs=[],
                    )
                    seq += 1
        usage = ResponseUsage(
            input_tokens=10, output_tokens=5, total_tokens=15,
            input_tokens_details=InputTokensDetails(cached_tokens=0, cache_write_tokens=0),
            output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        )
        response = Response.model_construct(id="resp_1", object="response", output=output, usage=usage, status="completed")
        yield ResponseCompletedEvent(type="response.completed", response=response, sequence_number=seq)


class FakeModels:
    def __init__(self, model: ScriptedModel):
        self.scripted = model

    async def model(self) -> ScriptedModel:
        return self.scripted

    async def close(self) -> None:
        return None
