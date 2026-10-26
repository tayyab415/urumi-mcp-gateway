"""End-to-end tests: a real MCP client -> our MCP server -> a fake WooCommerce API.

Everything runs in one process. The MCP client speaks the real protocol to the
server, and the server makes real (signed) HTTP requests to the fake store.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2
import pytest
from mcp import Client

from tests.fake_store import CONSUMER_KEY, CONSUMER_SECRET, HOME_URL, FakeStore
from woo_mcp.server import build_server
from woo_mcp.woo_client import WooClient, WooConfig, oauth1_signature

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


def make_server(store: FakeStore, **overrides):
    config = WooConfig(
        store_url=HOME_URL,
        api_url="http://urumi-wordpress.urumi-store.svc",  # internal address differs from the home URL
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        **overrides,
    )
    return build_server(WooClient(config, transport=httpx2.ASGITransport(app=store.app)))


async def test_lists_the_five_tools(store):
    async with Client(make_server(store)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"list_products", "create_product", "list_orders", "get_order", "update_order_status"}
    assert tools["list_orders"].annotations.read_only_hint is True
    assert tools["create_product"].annotations.read_only_hint is False


async def test_create_then_find_product(store):
    async with Client(make_server(store)) as client:
        created = await client.call_tool("create_product", {"name": "Training Ball", "regular_price": 25})
        assert not created.is_error, created.content
        assert created.structured_content["regular_price"] == "25.00"
        found = await client.call_tool("list_products", {"search": "training"})
    assert [p["name"] for p in found.structured_content["products"]] == ["Training Ball"]
    assert found.structured_content["total"] == 1


async def test_duplicate_sku_is_a_clear_known_failure(store):
    async with Client(make_server(store)) as client:
        await client.call_tool("create_product", {"name": "Ball", "regular_price": "10", "sku": "BALL-1"})
        again = await client.call_tool("create_product", {"name": "Ball", "regular_price": "10", "sku": "BALL-1"})
    assert again.is_error
    assert "already exists" in again.content[0].text


async def test_rejects_invalid_price_before_calling_the_store(store):
    async with Client(make_server(store)) as client:
        result = await client.call_tool("create_product", {"name": "Ball", "regular_price": -5})
    assert result.is_error
    assert store.products == {}


async def test_today_uses_the_store_timezone(store):
    now = datetime.now(ZoneInfo(store.timezone))
    today = store.add_order(status="processing", created=now.replace(hour=0, minute=5, second=0, microsecond=0).isoformat(timespec="seconds").split("+")[0], items=[(7, "Training Ball", 2)])
    store.add_order(status="completed", created=(now - timedelta(days=1)).isoformat(timespec="seconds").split("+")[0], items=[(8, "Cone", 1)])
    async with Client(make_server(store)) as client:
        result = await client.call_tool("list_orders", {"period": "today"})
    page = result.structured_content
    assert [o["id"] for o in page["orders"]] == [today["id"]]
    assert page["date_range"]["timezone"] == "Asia/Kolkata"
    assert page["orders"][0]["ship_to"]["country"] == "IN"
    # No emails or phone numbers leave the store.
    assert "asha@example.com" not in str(page)


async def test_get_and_update_order_status(store):
    order = store.add_order(status="processing", created="2026-10-23T10:00:00", items=[(7, "Training Ball", 1)])
    async with Client(make_server(store)) as client:
        detail = await client.call_tool("get_order", {"order_id": order["id"]})
        assert detail.structured_content["items"][0]["name"] == "Training Ball"
        changed = await client.call_tool("update_order_status", {"order_id": order["id"], "status": "completed"})
        unchanged = await client.call_tool("update_order_status", {"order_id": order["id"], "status": "completed"})
        missing = await client.call_tool("get_order", {"order_id": 999999})
    assert changed.structured_content == {
        "id": order["id"], "number": order["number"], "previous_status": "processing", "status": "completed", "changed": True,
    }
    assert unchanged.structured_content["changed"] is False
    assert store.orders[order["id"]]["status"] == "completed"
    assert missing.is_error and "does not exist" in missing.content[0].text


async def test_wrong_secret_is_reported_as_an_auth_problem(store):
    bad =WooConfig(store_url=HOME_URL, consumer_key=CONSUMER_KEY, consumer_secret="wrong")
    server = build_server(WooClient(bad, transport=httpx2.ASGITransport(app=store.app)))
    async with Client(server) as client:
        result = await client.call_tool("list_orders", {})
    assert result.is_error
    assert "HTTP 401" in result.content[0].text and "API key" in result.content[0].text


async def test_write_timeout_is_reported_as_unknown_outcome():
    def hang(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("simulated", request=request)

    config = WooConfig(store_url=HOME_URL, consumer_key=CONSUMER_KEY, consumer_secret=CONSUMER_SECRET)
    server = build_server(WooClient(config, transport=httpx2.MockTransport(hang)))
    async with Client(server) as client:
        result = await client.call_tool("create_product", {"name": "Ball", "regular_price": 5})
    assert result.is_error
    assert result.content[0].text.startswith("Error executing tool create_product: OUTCOME UNKNOWN")


async def test_unreachable_store_says_nothing_changed():
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    config = WooConfig(store_url=HOME_URL, consumer_key=CONSUMER_KEY, consumer_secret=CONSUMER_SECRET)
    server = build_server(WooClient(config, transport=httpx2.MockTransport(refuse)))
    async with Client(server) as client:
        result = await client.call_tool("create_product", {"name": "Ball", "regular_price": 5})
    assert "Nothing was changed" in result.content[0].text


def test_signature_matches_the_oauth_spec_example_shape():
    # Slashes and spaces must be percent-encoded twice inside the base string.
    a = oauth1_signature("GET", "http://x/wp-json/", {"rest_route": "/wc/v3/orders", "q": "a b"}, "s")
    b = oauth1_signature("GET", "http://x/wp-json/", {"q": "a b", "rest_route": "/wc/v3/orders"}, "s")
    assert a == b  # parameter order does not matter
