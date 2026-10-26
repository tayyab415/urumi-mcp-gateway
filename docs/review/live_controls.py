shipping = next(s for s in api("admin", "/api/servers")[1] if s["slug"] == "shipping")
STATE["shipping_before"] = {"id": shipping["id"], "enabled": shipping["enabled"]}
status, result, _ = api("admin", f"/api/servers/{shipping['id']}", "PATCH", {"enabled": False})
emit("Disable shipping", {"status": status, "enabled": result.get("enabled")})
status, result, _ = rpc("bob")
emit("Bob's next list shipping tools", {
    "status": status,
    "tools": [t["name"] for t in result.get("result", {}).get("tools", []) if t["name"].startswith("shipping__")],
})
status, result, _ = rpc("bob", "tools/call", {"name": "shipping__list_carriers", "arguments": {}})
emit("Bob's cached direct call", {"status": status, "result": result.get("result")})
