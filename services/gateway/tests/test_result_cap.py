"""The result cap counts the whole result, and an upstream cannot make the gateway read without limit."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager

import anyio
import httpx2
import mcp_types as types
import pytest
from mcp.server import Server as LowLevelServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from starlette.applications import Starlette
from starlette.routing import Route

from gateway import upstreams
from gateway.core import MAX_RESULT_CHARS, _capped
from gateway.mcp_endpoint import session_manager
from tests.test_gateway import serving

pytestmark = pytest.mark.anyio

IMAGE = base64.b64encode(b"\x89PNG" + b"\x00" * 300_000).decode()  # 400,000 characters of base64


@pytest.fixture
def anyio_backend():
    return "asyncio"


def size(result: types.CallToolResult) -> int:
    return len(result.model_dump_json(by_alias=True, exclude_none=True))


def note(result: types.CallToolResult) -> str:
    return result.content[-1].text


def text(value: str) -> types.TextContent:
    return types.TextContent(type="text", text=value)


async def test_a_small_result_passes_unchanged():
    result = types.CallToolResult(content=[text("ok")], structured_content={"orders": [1, 2]})
    assert _capped(result) is result


async def test_large_structured_content_is_left_out_and_said():
    result = types.CallToolResult(content=[text("ok")], structured_content={"payload": "x" * 300_000})
    capped = _capped(result)
    assert size(capped) <= MAX_RESULT_CHARS
    assert capped.structured_content is None and capped.content[0].text == "ok"
    assert "300" in note(capped) and "the structured content" in note(capped)
    assert not capped.is_error
    # With an output schema, a client would refuse a "successful" result that lacks it.
    assert _capped(result, has_output_schema=True).is_error


async def test_an_image_over_the_limit_is_left_out():
    result = types.CallToolResult(content=[text("chart below"), types.ImageContent(type="image", data=IMAGE, mime_type="image/png")])
    capped = _capped(result)
    assert size(capped) <= MAX_RESULT_CHARS
    assert [c.type for c in capped.content] == ["text", "text"] and "an image" in note(capped)


async def test_an_embedded_resource_counts_too():
    resource = types.EmbeddedResource(type="resource", resource=types.TextResourceContents(uri="file:///big.txt", text="y" * 300_000))
    capped = _capped(types.CallToolResult(content=[resource]))
    assert size(capped) <= MAX_RESULT_CHARS and "an embedded resource" in note(capped)


async def test_text_is_cut_to_fit_even_when_json_escaping_makes_it_longer():
    for body in ("x" * 300_000, '"\n' * 150_000):
        capped = _capped(types.CallToolResult(content=[text(body)]))
        assert size(capped) <= MAX_RESULT_CHARS
        assert capped.content[0].text and body.startswith(capped.content[0].text)
        assert "the rest of the text" in note(capped)


async def test_many_small_parts_count_their_envelopes():
    capped = _capped(types.CallToolResult(content=[text("z")] * 40_000))
    assert size(capped) <= MAX_RESULT_CHARS


# -------------------------------------------------- bytes read from an upstream


def stream_of(*chunks: bytes) -> httpx2.AsyncByteStream:
    class Chunks(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for chunk in chunks:
                yield chunk

    return Chunks()


async def test_the_byte_budget_stops_reading_and_refuses_compressed_answers():
    target = upstreams.Target(slug="big", url="http://big.invalid/mcp", timeout=5)
    budget = upstreams._ByteBudget(target, limit=10)
    response = httpx2.Response(200, stream=stream_of(b"12345", b"67890", b"overflow"))
    budget.watch(response)
    with pytest.raises(httpx2.StreamError):
        await response.aread()
    assert "stopped reading" in budget.problem

    zipped = upstreams._ByteBudget(target, limit=10_000)
    response = httpx2.Response(200, headers={"content-encoding": "gzip"}, stream=stream_of(b"\x1f\x8b"))
    zipped.watch(response)
    with pytest.raises(httpx2.StreamError):
        await response.aread()
    assert "compressed" in zipped.problem


def big_upstream() -> Starlette:
    async def listing(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(name="dump", input_schema={"type": "object"})])

    async def calling(ctx, params):
        return types.CallToolResult(content=[text("x" * 300_000)])

    manager = session_manager(LowLevelServer("big", on_list_tools=listing, on_call_tool=calling), [], [])

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    return Starlette(routes=[Route("/mcp", StreamableHTTPASGIApp(manager), methods=["POST"])], lifespan=lifespan)


async def test_an_upstream_answer_over_the_byte_limit_is_a_clear_failure(monkeypatch):
    async with anyio.create_task_group() as tg:
        async with serving(tg, big_upstream()) as (url, _):
            target = upstreams.Target(slug="big", url=f"{url}/mcp", timeout=5, allow_loopback=True)
            assert [t.name for t in (await upstreams.discover(target)).tools] == ["dump"]
            assert len((await upstreams.call_tool(target, "dump", {})).content[0].text) == 300_000

            monkeypatch.setattr(upstreams, "MAX_RESPONSE_BYTES", 100_000)
            with pytest.raises(upstreams.UpstreamFailure) as failure:
                await upstreams.call_tool(target, "dump", {})
            assert failure.value.kind == "protocol" and "stopped reading" in failure.value.message
        tg.cancel_scope.cancel()
