"""Shipping quotes MCP server. SIMULATED rates, not a real carrier API.

Rates are a fixed table so answers are repeatable in a demo. The point of this
server is the MCP and gateway behavior around it, not the carrier data.

Run: SHIPPING_API_TOKEN=... uvicorn demo_mcps.shipping:app --port 8000
Callers must send `Authorization: Bearer <SHIPPING_API_TOKEN>`.
"""

from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from demo_mcps.common import serve

ORIGIN = os.environ.get("SHIPPING_ORIGIN_COUNTRY", "IN")
CURRENCY = os.environ.get("SHIPPING_CURRENCY", "INR")

SOUTH_ASIA = {"BD", "BT", "LK", "MV", "NP", "PK"}
ZONES = {
    # zone: (carrier, service, base, per_kg, eta_days)
    "domestic": [("IndiaPost", "Speed Post", "60", "30", 4), ("Delhivery", "Surface", "80", "25", 3), ("BlueDart", "Express", "180", "60", 1)],
    "south_asia": [("IndiaPost", "International", "650", "220", 9), ("DHL", "Express", "1900", "450", 3)],
    "international": [("IndiaPost", "International", "1100", "420", 14), ("DHL", "Express", "2900", "700", 4)],
}


class ShippingOption(BaseModel):
    carrier: str
    service: str
    price: str
    currency: str
    eta_days: int
    express: bool


class Quote(BaseModel):
    destination_country: str
    destination_postcode: str | None
    weight_kg: float
    zone: str
    options: list[ShippingOption] = Field(description="Cheapest first")
    simulated: bool = True


def _zone(country: str) -> str:
    if country == ORIGIN:
        return "domestic"
    return "south_asia" if country in SOUTH_ASIA else "international"


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


mcp = MCPServer(
    "shipping",
    title="Shipping quotes (simulated)",
    version="0.1.0",
    instructions=f"Simulated parcel rates from {ORIGIN}. Use an order's destination country and postcode.",
)


@mcp.tool(title="Quote shipping", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def quote_shipping(
    country: Annotated[str, Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2 code, e.g. IN or US")],
    weight_kg: Annotated[float, Field(gt=0, le=70)] = 1.0,
    postcode: Annotated[str | None, Field(max_length=20)] = None,
    express_only: bool = False,
) -> Quote:
    """Quote parcel shipping options to a destination. Rates are simulated."""
    country = country.upper()
    if not country.isalpha():
        raise ToolError("country must be a two-letter ISO code such as IN or US.")
    zone = _zone(country)
    weight = Decimal(str(weight_kg))
    options = [
        ShippingOption(
            carrier=carrier,
            service=service,
            price=_money(Decimal(base) + Decimal(per_kg) * weight),
            currency=CURRENCY,
            eta_days=eta,
            express=service == "Express",
        )
        for carrier, service, base, per_kg, eta in ZONES[zone]
    ]
    if express_only:
        options = [o for o in options if o.express]
    options.sort(key=lambda o: Decimal(o.price))
    return Quote(destination_country=country, destination_postcode=postcode, weight_kg=weight_kg, zone=zone, options=options)


@mcp.tool(title="List carriers", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def list_carriers() -> dict[str, Any]:
    """List the carriers and services available per zone."""
    return {
        "origin_country": ORIGIN,
        "zones": {zone: [f"{c} {s}" for c, s, *_ in rows] for zone, rows in ZONES.items()},
        "simulated": True,
    }


app = serve(mcp, header="authorization", credential_env="SHIPPING_API_TOKEN", value_prefix="Bearer ")
