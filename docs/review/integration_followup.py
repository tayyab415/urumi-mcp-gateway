for name, arguments in [
    ("store-north__list_products", {"per_page": 1}),
    ("store-south__list_products", {"per_page": 1}),
    ("store-west-sports__list_products", {"per_page": 1}),
    ("store-review-astra__get_order", {"order_id": 16}),
    ("supplier__quote_restock", {"items": [{"sku": "SUP-BALL-5", "quantity": 10}]}),
    ("shipping__quote_shipping", {"country": "IN", "postcode": "400001", "weight_kg": 1}),
    ("slack__read_messages", {"limit": 1}),
]:
    status, body, _ = rpc("admin", "tools/call", {"name": name, "arguments": arguments})
    emit(name, {"http": status, "result": body.get("result", body)})
status, body, _ = rpc("admin", "tools/call", {
    "name": "store-review-astra__list_products",
    "arguments": {"search": "Review Unauthorized Ball"},
})
emit("Injection produced no product", {"http": status, "total": body["result"]["structuredContent"]["total"]})
status, body, _ = api("admin", "/api/audit?server=store-review-astra&limit=30")
emit("Review store audit", [
    {k: row[k] for k in ("id", "user_email", "via", "public_name", "arguments", "outcome", "duration_ms")}
    for row in body
])
