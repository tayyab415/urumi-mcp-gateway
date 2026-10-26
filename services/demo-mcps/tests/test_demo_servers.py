from __future__ import annotations

import httpx2
import pytest
from mcp import Client
from starlette.responses import PlainTextResponse

from demo_mcps import shipping, supplier
from demo_mcps.common import StaticCredential

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_shipping_quotes_are_deterministic_and_sorted():
    async with Client(shipping.mcp) as client:
        a = await client.call_tool("quote_shipping", {"country": "us", "weight_kg": 2})
        b = await client.call_tool("quote_shipping", {"country": "US", "weight_kg": 2})
        home = await client.call_tool("quote_shipping", {"country": "IN", "weight_kg": 1})
    assert a.structured_content == b.structured_content
    prices = [float(o["price"]) for o in a.structured_content["options"]]
    assert prices == sorted(prices)
    assert home.structured_content["zone"] == "domestic"


async def test_supplier_quote_flags_unfulfillable_lines():
    async with Client(supplier.mcp) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        quote = await client.call_tool("quote_restock", {"items": [{"sku": "SUP-BALL-5", "quantity": 20}, {"sku": "SUP-PUMP", "quantity": 10}]})
        po = await client.call_tool("create_purchase_order", {"items": [{"sku": "SUP-PUMP", "quantity": 10}]})
    assert "list_products" in tools  # same name as the WooCommerce tool, on purpose
    lines = {line["sku"]: line for line in quote.structured_content["lines"]}
    assert lines["SUP-BALL-5"]["can_fulfil"] and not lines["SUP-PUMP"]["can_fulfil"]
    assert quote.structured_content["total"] == "230.00"  # 20 balls at 11.50; the pump line cannot ship, so it is not counted
    assert po.is_error and "SUP-PUMP" in po.content[0].text


async def test_static_credential_middleware():
    async def ok(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    app = StaticCredential(ok, header="authorization", expected="Bearer s3cret")
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://t") as http:
        assert (await http.post("/mcp")).status_code == 401
        assert (await http.post("/mcp", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await http.post("/mcp", headers={"Authorization": "Bearer s3cret"})).status_code == 200
        assert (await http.get("/healthz")).status_code == 200
