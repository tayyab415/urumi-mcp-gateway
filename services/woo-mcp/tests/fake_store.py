"""A stand-in for WooCommerce's REST API, for tests that run without a store.

It implements the five endpoints the MCP server uses, the pagination headers,
and WooCommerce's OAuth 1.0a check, ported line by line from
WC_REST_Authentication::check_oauth_signature. It verifies our side of the
protocol. It does not replace a test against a real store.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote, unquote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

CONSUMER_KEY = "ck_test"
CONSUMER_SECRET = "cs_test"
HOME_URL = "http://shop.urumi.test:18080"  # what WordPress thinks its address is


def _php_rawurlencode(s: str) -> str:
    return quote(s, safe="-_.~")


def _expected_signature(method: str, path: str, params: dict[str, str]) -> str:
    params = dict(params)
    params.pop("oauth_signature", None)
    # uksort($params, 'strcmp'), then normalize_parameters(): decode, encode, double-encode %.
    normalized = {
        _php_rawurlencode(unquote(k)).replace("%", "%25"): _php_rawurlencode(unquote(v)).replace("%", "%25")
        for k, v in sorted(params.items())
    }
    query = "%26".join(f"{k}%3D{v}" for k, v in normalized.items())
    # The base URL comes from get_home_url() plus the request path, not the Host header.
    base_uri = _php_rawurlencode(f"{HOME_URL}{path}")
    to_sign = f"{method}&{base_uri}&{query}"
    digest = hmac.new(f"{CONSUMER_SECRET}&".encode(), to_sign.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class FakeStore:
    def __init__(self, timezone: str = "Asia/Kolkata"):
        self.timezone = timezone
        self.products: dict[int, dict] = {}
        self.orders: dict[int, dict] = {}
        self.next_id = 100
        self.seen_nonces: set[str] = set()
        self.app = Starlette(
            routes=[
                Route("/wp-json/", self.index),
                Route("/wp-json/wc/v3/products", self.products_route, methods=["GET", "POST"]),
                Route("/wp-json/wc/v3/orders", self.list_orders, methods=["GET"]),
                Route("/wp-json/wc/v3/orders/{order_id:int}", self.order_route, methods=["GET", "PUT"]),
            ]
        )

    # -- helpers ---------------------------------------------------------------

    def _new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    # Where each country's sample customer lives, so city, state and country agree.
    ADDRESSES = {
        "IN": ("Pune", "MH", "411001"),
        "US": ("Austin", "TX", "73301"),
        "LK": ("Colombo", "", "00100"),
    }

    def add_order(self, *, status: str, created: str, items: list[tuple[int, str, int]], country: str = "IN") -> dict:
        oid = self._new_id()
        city, state, postcode = self.ADDRESSES.get(country, ("Pune", "MH", "411001"))
        lines, total = [], Decimal("0")
        for pid, name, qty in items:
            price = Decimal(self.products.get(pid, {}).get("price") or "25.00")
            lines.append({"product_id": pid, "name": name, "quantity": qty, "total": f"{price * qty:.2f}"})
            total += price * qty
        self.orders[oid] = {
            "id": oid,
            "number": str(oid),
            "status": status,
            "date_created": created,
            "total": f"{total:.2f}",
            "currency": "INR",
            "billing": {"first_name": "Asha", "last_name": "Rao", "email": "asha@example.com", "phone": "999",
                        "city": city, "state": state, "postcode": postcode, "country": country},
            "shipping": {"first_name": "", "last_name": "", "city": "", "state": "", "postcode": "", "country": ""},
            "line_items": lines,
            "payment_method_title": "Cash on delivery",
            "shipping_total": "0.00",
            "discount_total": "0.00",
            "total_tax": "0.00",
            "customer_note": "",
            "date_paid": None,
            "date_completed": None,
        }
        return self.orders[oid]

    def _auth(self, request: Request) -> JSONResponse | None:
        params = dict(request.query_params)
        if params.get("oauth_consumer_key") != CONSUMER_KEY:
            return JSONResponse({"code": "woocommerce_rest_authentication_error", "message": "Consumer key is invalid."}, 401)
        if params.get("oauth_signature") != _expected_signature(request.method, request.url.path, params):
            return JSONResponse({"code": "woocommerce_rest_authentication_error", "message": "Invalid signature - provided signature does not match."}, 401)
        nonce = params.get("oauth_nonce", "")
        if nonce in self.seen_nonces:
            return JSONResponse({"code": "woocommerce_rest_authentication_error", "message": "Invalid nonce - nonce has already been used."}, 401)
        self.seen_nonces.add(nonce)
        return None

    @staticmethod
    def _page(items: list[dict], request: Request) -> JSONResponse:
        page = int(request.query_params.get("page", 1))
        per_page = int(request.query_params.get("per_page", 10))
        chunk = items[(page - 1) * per_page : page * per_page]
        pages = (len(items) + per_page - 1) // per_page
        return JSONResponse(chunk, headers={"X-WP-Total": str(len(items)), "X-WP-TotalPages": str(pages)})

    # -- routes ----------------------------------------------------------------

    async def index(self, request: Request) -> JSONResponse:
        return JSONResponse({"name": "Urumi test shop", "timezone_string": self.timezone, "gmt_offset": 5.5})

    async def products_route(self, request: Request) -> JSONResponse:
        if denied := self._auth(request):
            return denied
        if request.method == "GET":
            items = sorted(self.products.values(), key=lambda p: -p["id"])
            if search := request.query_params.get("search"):
                items = [p for p in items if search.lower() in p["name"].lower()]
            if sku := request.query_params.get("sku"):
                items = [p for p in items if p["sku"] == sku]
            return self._page(items, request)
        body = await request.json()
        if body.get("sku") and any(p["sku"] == body["sku"] for p in self.products.values()):
            return JSONResponse({"code": "product_invalid_sku", "message": "Invalid or duplicated SKU."}, 400)
        pid = self._new_id()
        self.products[pid] = {
            "id": pid,
            "name": body["name"],
            "sku": body.get("sku", ""),
            "status": body.get("status", "publish"),
            "price": body["regular_price"],
            "regular_price": body["regular_price"],
            "stock_status": "instock",
            "stock_quantity": body.get("stock_quantity"),
            "permalink": f"{HOME_URL}/product/{body['name'].lower().replace(' ', '-')}/",
        }
        return JSONResponse(self.products[pid], 201)

    async def list_orders(self, request: Request) -> JSONResponse:
        if denied := self._auth(request):
            return denied
        q = request.query_params
        items = sorted(self.orders.values(), key=lambda o: o["date_created"], reverse=True)
        if q.get("status", "any") != "any":
            items = [o for o in items if o["status"] == q["status"]]
        if q.get("product"):
            items = [o for o in items if any(i["product_id"] == int(q["product"]) for i in o["line_items"])]
        if q.get("after"):
            items = [o for o in items if datetime.fromisoformat(o["date_created"]) > datetime.fromisoformat(q["after"])]
        if q.get("before"):
            items = [o for o in items if datetime.fromisoformat(o["date_created"]) < datetime.fromisoformat(q["before"])]
        return self._page(items, request)

    async def order_route(self, request: Request) -> JSONResponse:
        if denied := self._auth(request):
            return denied
        order = self.orders.get(request.path_params["order_id"])
        if order is None:
            return JSONResponse({"code": "woocommerce_rest_shop_order_invalid_id", "message": "Invalid ID."}, 404)
        if request.method == "PUT":
            order["status"] = (await request.json())["status"]
        return JSONResponse(order)
