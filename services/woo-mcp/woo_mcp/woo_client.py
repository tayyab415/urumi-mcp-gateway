"""Small async client for the WooCommerce REST API v3.

Only this module talks to the store. The MCP tools in server.py call it and
never see the consumer key or secret.

Authentication
--------------
WooCommerce accepts two schemes:

* HTTP Basic with the consumer key and secret, but only when WordPress believes
  the request arrived over HTTPS (`is_ssl()`).
* One-legged OAuth 1.0a over plain HTTP. Each request carries a timestamp,
  a nonce and an HMAC-SHA256 signature in its query string.

Inside the cluster we call WordPress over plain HTTP on its Service, so the
default is OAuth 1.0a. WooCommerce rebuilds the signed URL from the store's
*home URL*, not from the Host header we sent. That is why the client keeps two
URLs: `api_url`, where requests go, and `store_url`, which is signed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx2


class WooError(Exception):
    """The store answered, and the answer was an error. Nothing is uncertain."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class WooUnreachable(WooError):
    """We could not connect. The request never reached WooCommerce."""


class WooOutcomeUnknown(WooError):
    """The request was sent but no answer came back in time.

    For a write, the store may or may not have applied it.
    """


def _enc(value: str) -> str:
    # PHP's rawurlencode: everything except A-Z a-z 0-9 - _ . ~ is escaped.
    return quote(value, safe="~")


def oauth1_signature(method: str, url: str, params: dict[str, str], consumer_secret: str) -> str:
    """HMAC-SHA256 signature exactly as WooCommerce recomputes it.

    Mirrors WC_REST_Authentication::check_oauth_signature: sort by key, encode
    each key and value, encode the percent signs a second time, join with
    %3D and %26, then sign "METHOD&encoded-url&params" with "secret&".
    """
    pairs = sorted(params.items())
    normalized = [f"{_enc(k).replace('%', '%25')}%3D{_enc(v).replace('%', '%25')}" for k, v in pairs]
    base = f"{method.upper()}&{_enc(url)}&{'%26'.join(normalized)}"
    digest = hmac.new(f"{consumer_secret}&".encode(), base.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


@dataclass(frozen=True)
class WooConfig:
    store_url: str  # the store's WordPress home URL, e.g. http://shop.urumi.test:18080
    consumer_key: str
    consumer_secret: str
    api_url: str | None = None  # where to send requests; defaults to store_url
    auth: Literal["oauth1", "basic"] = "oauth1"
    timeout_seconds: float = 15.0


class WooClient:
    def __init__(self, config: WooConfig, transport: httpx2.AsyncBaseTransport | None = None):
        self.config = config
        self._store = config.store_url.rstrip("/")
        self._api = (config.api_url or config.store_url).rstrip("/")
        self._http = httpx2.AsyncClient(
            timeout=httpx2.Timeout(config.timeout_seconds),
            transport=transport,
            headers={"User-Agent": "urumi-woo-mcp/0.1"},
        )
        self._tz: tzinfo | None = None
        self._tz_fetched_at = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> tuple[Any, httpx2.Headers]:
        """Call /wp-json/wc/v3{path}. Returns (decoded JSON, response headers)."""
        route = f"/wp-json/wc/v3{path}"
        query = {k: _as_query_value(v) for k, v in (params or {}).items() if v is not None}
        auth = None
        if self.config.auth == "basic":
            auth = httpx2.BasicAuth(self.config.consumer_key, self.config.consumer_secret)
        else:
            query |= {
                "oauth_consumer_key": self.config.consumer_key,
                "oauth_timestamp": str(int(time.time())),
                "oauth_nonce": secrets.token_hex(16),
                "oauth_signature_method": "HMAC-SHA256",
            }
            query["oauth_signature"] = oauth1_signature(
                method, f"{self._store}{route}", query, self.config.consumer_secret
            )

        try:
            response = await self._http.request(method, f"{self._api}{route}", params=query, json=json, auth=auth)
        except (httpx2.ConnectError, httpx2.ConnectTimeout) as exc:
            raise WooUnreachable(f"Cannot reach the store at {self._api}: {exc.__class__.__name__}") from exc
        except httpx2.TimeoutException as exc:
            raise WooOutcomeUnknown(
                f"The store did not answer within {self.config.timeout_seconds:g}s."
            ) from exc

        if response.status_code >= 400:
            raise _error_from(response)
        try:
            return response.json(), response.headers
        except ValueError as exc:
            raise WooError(f"The store returned non-JSON content (HTTP {response.status_code}).") from exc

    async def store_timezone(self) -> tzinfo:
        """The store's configured timezone, read from the public REST index.

        "Today" for a shop owner means today in the shop's timezone, not the
        server's. Cached for ten minutes.
        """
        if self._tz is not None and time.monotonic() - self._tz_fetched_at < 600:
            return self._tz
        try:
            response = await self._http.get(f"{self._api}/wp-json/")
            response.raise_for_status()
            index = response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise WooUnreachable(f"Cannot read the store's timezone: {exc.__class__.__name__}") from exc
        name = index.get("timezone_string") or ""
        if name:
            tz: tzinfo = ZoneInfo(name)
        else:
            tz = timezone(timedelta(hours=float(index.get("gmt_offset") or 0)))
        self._tz, self._tz_fetched_at = tz, time.monotonic()
        return tz


def _as_query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _error_from(response: httpx2.Response) -> WooError:
    code = message = None
    try:
        body = response.json()
        code, message = body.get("code"), body.get("message")
    except (ValueError, AttributeError):
        pass
    if response.status_code in (401, 403):
        hint = " Check the WooCommerce API key and its read/write permission."
    else:
        hint = ""
    text = message or response.reason_phrase or "error"
    return WooError(f"WooCommerce returned HTTP {response.status_code}: {text}.{hint}", response.status_code, code)
