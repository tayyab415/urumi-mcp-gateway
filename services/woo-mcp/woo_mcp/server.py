"""WooCommerce MCP server: five store tools over Streamable HTTP.

The gateway is this server's only intended caller. It reaches us on an
internal Service, and a NetworkPolicy blocks every other Pod. This process holds
the WooCommerce API credential; neither the gateway nor any user ever sees it.

Run locally:
    WOO_STORE_URL=http://shop.urumi.test:18080 WOO_CONSUMER_KEY=ck_... \
    WOO_CONSUMER_SECRET=cs_... uvicorn woo_mcp.server:app --port 8000
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

import httpx2
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from woo_mcp.woo_client import WooClient, WooConfig, WooError, WooOutcomeUnknown, WooUnreachable

OrderStatus = Literal["pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"]
Period = Literal["today", "yesterday", "last_7_days", "last_30_days"]

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


# ---------------------------------------------------------------------------
# Output shapes. We return a trimmed view of WooCommerce objects: enough to
# reconcile with WooCommerce admin, without emails, phone numbers or street
# addresses, which no tool here needs.
# ---------------------------------------------------------------------------


class Product(BaseModel):
    id: int
    name: str
    sku: str
    status: str
    price: str
    regular_price: str
    stock_status: str
    stock_quantity: int | None
    permalink: str


class ProductPage(BaseModel):
    products: list[Product]
    page: int
    per_page: int
    total: int
    total_pages: int


class LineItem(BaseModel):
    product_id: int
    name: str
    quantity: int
    total: str


class Destination(BaseModel):
    city: str
    state: str
    postcode: str
    country: str


class Order(BaseModel):
    id: int
    number: str
    status: str
    date_created: str = Field(description="Store local time")
    total: str
    currency: str
    customer_name: str
    items: list[LineItem]
    ship_to: Destination


class OrderDetail(Order):
    date_paid: str | None
    date_completed: str | None
    payment_method: str
    shipping_total: str
    discount_total: str
    total_tax: str
    customer_note: str


class DateRange(BaseModel):
    start: str
    end_exclusive: str
    timezone: str


class OrderPage(BaseModel):
    orders: list[Order]
    page: int
    per_page: int
    total: int
    total_pages: int
    date_range: DateRange | None = Field(description="The store-local window that was queried, if any")


class StatusChange(BaseModel):
    id: int
    number: str
    previous_status: str
    status: str
    changed: bool


def _product(p: dict[str, Any]) -> Product:
    return Product(
        id=p["id"],
        name=p.get("name", ""),
        sku=p.get("sku") or "",
        status=p.get("status", ""),
        price=p.get("price") or "",
        regular_price=p.get("regular_price") or "",
        stock_status=p.get("stock_status", ""),
        stock_quantity=p.get("stock_quantity"),
        permalink=p.get("permalink", ""),
    )


def _order_fields(o: dict[str, Any]) -> dict[str, Any]:
    billing = o.get("billing") or {}
    shipping = o.get("shipping") or {}
    # Fall back to the billing address when the order has no separate shipping address.
    ship = shipping if shipping.get("country") else billing
    return {
        "id": o["id"],
        "number": str(o.get("number", o["id"])),
        "status": o.get("status", ""),
        "date_created": o.get("date_created") or "",
        "total": o.get("total", ""),
        "currency": o.get("currency", ""),
        "customer_name": " ".join(filter(None, [billing.get("first_name"), billing.get("last_name")])) or "Guest",
        "items": [
            LineItem(
                product_id=i.get("product_id") or 0,
                name=i.get("name", ""),
                quantity=i.get("quantity", 0),
                total=i.get("total", ""),
            )
            for i in o.get("line_items") or []
        ],
        "ship_to": Destination(
            city=ship.get("city", ""),
            state=ship.get("state", ""),
            postcode=ship.get("postcode", ""),
            country=ship.get("country", ""),
        ),
    }


def _int_header(headers: httpx2.Headers, name: str) -> int:
    try:
        return int(headers.get(name, "0"))
    except ValueError:
        return 0


def _tool_error(exc: WooError, *, write: bool) -> ToolError:
    """Turn a store failure into a message the caller (often a model) can act on."""
    if isinstance(exc, WooOutcomeUnknown) and write:
        return ToolError(
            f"OUTCOME UNKNOWN. {exc} The store may already have applied this change. "
            "Do not retry blindly: read the current state first (list_products or get_order)."
        )
    if isinstance(exc, WooUnreachable):
        return ToolError(f"Store unavailable. {exc} Nothing was changed.")
    return ToolError(str(exc))


def build_server(woo: WooClient) -> MCPServer:
    mcp = MCPServer(
        "woocommerce",
        title="WooCommerce store",
        version="0.1.0",
        instructions=(
            "Tools for one WooCommerce store: read products and orders, create products, "
            "and change an order's status. Dates are in the store's timezone."
        ),
    )

    @mcp.tool(title="List products", annotations=READ_ONLY)
    async def list_products(
        search: Annotated[str | None, Field(description="Text to match in product names")] = None,
        sku: Annotated[str | None, Field(description="Exact SKU")] = None,
        status: Literal["any", "publish", "draft", "pending", "private"] = "any",
        page: Annotated[int, Field(ge=1)] = 1,
        per_page: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> ProductPage:
        """List store products, newest first. Use search to find a product by name."""
        params = {"search": search, "sku": sku, "status": status, "page": page, "per_page": per_page}
        try:
            body, headers = await woo.request("GET", "/products", params=params)
        except WooError as exc:
            raise _tool_error(exc, write=False) from exc
        return ProductPage(
            products=[_product(p) for p in body],
            page=page,
            per_page=per_page,
            total=_int_header(headers, "X-WP-Total"),
            total_pages=_int_header(headers, "X-WP-TotalPages"),
        )

    @mcp.tool(
        title="Create product",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False),
    )
    async def create_product(
        name: Annotated[str, Field(min_length=1, max_length=200)],
        regular_price: Annotated[Decimal, Field(ge=0, le=1_000_000, decimal_places=2, description="Price in the store currency, e.g. 25 or 25.00")],
        description: Annotated[str, Field(max_length=5000)] = "",
        sku: Annotated[str | None, Field(max_length=100, description="Optional unique SKU. The store rejects duplicates.")] = None,
        stock_quantity: Annotated[int | None, Field(ge=0, description="Track stock with this starting quantity")] = None,
        status: Literal["publish", "draft"] = "publish",
    ) -> Product:
        """Create a simple product. Published products appear on the storefront immediately."""
        payload: dict[str, Any] = {
            "name": name,
            "type": "simple",
            "status": status,
            "regular_price": f"{regular_price:.2f}",
            "description": description,
        }
        if sku:
            payload["sku"] = sku
        if stock_quantity is not None:
            payload |= {"manage_stock": True, "stock_quantity": stock_quantity}
        try:
            body, _ = await woo.request("POST", "/products", json=payload)
        except WooError as exc:
            if exc.code == "product_invalid_sku":
                raise ToolError(f"A product with SKU {sku!r} already exists. Nothing was created.") from exc
            raise _tool_error(exc, write=True) from exc
        return _product(body)

    @mcp.tool(title="List orders", annotations=READ_ONLY)
    async def list_orders(
        period: Annotated[Period | None, Field(description="Shortcut for a date window in the store's timezone")] = None,
        from_date: Annotated[date | None, Field(description="First day to include, store local (YYYY-MM-DD)")] = None,
        to_date: Annotated[date | None, Field(description="Last day to include, store local (YYYY-MM-DD)")] = None,
        status: Literal["any", "pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"] = "any",
        product_id: Annotated[int | None, Field(description="Only orders containing this product")] = None,
        search: Annotated[str | None, Field(description="Text to match in orders")] = None,
        page: Annotated[int, Field(ge=1)] = 1,
        per_page: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> OrderPage:
        """List orders, newest first. Use period="today" for today's orders in the store's timezone."""
        if period and (from_date or to_date):
            raise ToolError("Use either period or from_date/to_date, not both.")
        date_range = None
        params: dict[str, Any] = {
            "status": status,
            "product": product_id,
            "search": search,
            "page": page,
            "per_page": per_page,
            "orderby": "date",
            "order": "desc",
        }
        try:
            if period or from_date or to_date:
                tz = await woo.store_timezone()
                start, end = _window(period, from_date, to_date, datetime.now(tz).date())
                date_range = DateRange(start=start.isoformat(), end_exclusive=end.isoformat(), timezone=str(tz))
                # Without dates_are_gmt, WooCommerce reads these as store-local times.
                params |= {"after": f"{start.isoformat()}T00:00:00", "before": f"{end.isoformat()}T00:00:00"}
            body, headers = await woo.request("GET", "/orders", params=params)
        except WooError as exc:
            raise _tool_error(exc, write=False) from exc
        return OrderPage(
            orders=[Order(**_order_fields(o)) for o in body],
            page=page,
            per_page=per_page,
            total=_int_header(headers, "X-WP-Total"),
            total_pages=_int_header(headers, "X-WP-TotalPages"),
            date_range=date_range,
        )

    @mcp.tool(title="Get order", annotations=READ_ONLY)
    async def get_order(order_id: Annotated[int, Field(ge=1)]) -> OrderDetail:
        """Get one order with its items, totals and destination."""
        try:
            o, _ = await woo.request("GET", f"/orders/{order_id}")
        except WooError as exc:
            if exc.status == 404:
                raise ToolError(f"Order {order_id} does not exist.") from exc
            raise _tool_error(exc, write=False) from exc
        return OrderDetail(
            **_order_fields(o),
            date_paid=o.get("date_paid"),
            date_completed=o.get("date_completed"),
            payment_method=o.get("payment_method_title") or o.get("payment_method") or "",
            shipping_total=o.get("shipping_total", ""),
            discount_total=o.get("discount_total", ""),
            total_tax=o.get("total_tax", ""),
            customer_note=o.get("customer_note") or "",
        )

    @mcp.tool(
        title="Update order status",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True),
    )
    async def update_order_status(order_id: Annotated[int, Field(ge=1)], status: OrderStatus) -> StatusChange:
        """Set an order's status, e.g. processing -> completed. Setting the current status changes nothing."""
        try:
            current, _ = await woo.request("GET", f"/orders/{order_id}")
        except WooError as exc:
            if exc.status == 404:
                raise ToolError(f"Order {order_id} does not exist.") from exc
            raise _tool_error(exc, write=False) from exc
        previous = current.get("status", "")
        number = str(current.get("number", order_id))
        if previous == status:
            return StatusChange(id=order_id, number=number, previous_status=previous, status=status, changed=False)
        try:
            updated, _ = await woo.request("PUT", f"/orders/{order_id}", json={"status": status})
        except WooError as exc:
            raise _tool_error(exc, write=True) from exc
        return StatusChange(
            id=order_id, number=number, previous_status=previous, status=updated.get("status", status), changed=True
        )

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> JSONResponse:
        # Liveness: this process answers. The store's health is reported per tool call
        # instead, so a store outage does not make Kubernetes restart this Pod.
        return JSONResponse({"status": "ok"})

    return mcp


def _window(period: str | None, from_date: date | None, to_date: date | None, today: date) -> tuple[date, date]:
    """Return [start, end) as store-local dates."""
    if period == "today":
        return today, today + timedelta(days=1)
    if period == "yesterday":
        return today - timedelta(days=1), today
    if period == "last_7_days":
        return today - timedelta(days=6), today + timedelta(days=1)
    if period == "last_30_days":
        return today - timedelta(days=29), today + timedelta(days=1)
    start = from_date or date(1970, 1, 1)
    end = (to_date or today) + timedelta(days=1)
    if end <= start:
        raise ToolError("to_date must not be before from_date.")
    return start, end


def config_from_env() -> WooConfig:
    store_url = os.environ["WOO_STORE_URL"]
    auth = os.environ.get("WOO_AUTH") or ("basic" if store_url.startswith("https://") else "oauth1")
    if auth not in ("oauth1", "basic"):
        raise ValueError("WOO_AUTH must be oauth1 or basic")
    return WooConfig(
        store_url=store_url,
        api_url=os.environ.get("WOO_API_URL") or None,
        consumer_key=os.environ["WOO_CONSUMER_KEY"],
        consumer_secret=os.environ["WOO_CONSUMER_SECRET"],
        auth=auth,  # type: ignore[arg-type]
        timeout_seconds=float(os.environ.get("WOO_TIMEOUT_SECONDS", "15")),
    )


def create_app(woo: WooClient) -> Starlette:
    # httpx2 logs every URL at INFO, and our URLs carry OAuth parameters.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    # Stateless + JSON responses: every POST is independent, so any replica can
    # answer and a restarted Pod has no sessions to lose.
    # DNS-rebinding protection guards servers on a developer's localhost against
    # browsers. Here the Host header is the cluster Service name, and the
    # NetworkPolicy decides who may connect, so the check is turned off.
    return build_server(woo).streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host="0.0.0.0",
    )


def __getattr__(name: str) -> Any:
    # `uvicorn woo_mcp.server:app` builds the app on first access, so importing
    # this module in tests does not require the WOO_* environment variables.
    if name == "app":
        return create_app(WooClient(config_from_env()))
    raise AttributeError(name)
