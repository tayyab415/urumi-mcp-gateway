before = api("bob", "/api/audit?limit=1")[1]
last_id = before[0]["id"] if before else 0
calls = [
    ("gateway__search_tools", {"query": "review-search-proof products"}),
    ("gateway__call_tool", {"name": "woo__list_products", "arguments": {"search": "review-search-proof"}}),
    ("gateway__call_tool", {"name": "gateway__search_tools", "arguments": {}}),
]
for name, arguments in calls:
    status, body, _ = rpc("bob", "tools/call", {"name": name, "arguments": arguments}, path="/mcp/search")
    emit("Call", {"name": name, "http": status, "isError": body.get("result", {}).get("isError")})
status, rows, _ = api("bob", f"/api/audit?after_id={last_id}")
emit("New rows after three calls", [
    {k: row[k] for k in ("public_name", "via", "outcome", "arguments")}
    for row in rows
])
status, rows, _ = api("bob", f"/api/audit?after_id={last_id}&via=mcp")
emit("New rows with MCP clients filter", {"http": status, "rows": rows})
