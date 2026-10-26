before = STATE["shipping_before"]
status, body, _ = api("admin", f"/api/servers/{before['id']}", "PATCH", {"enabled": before["enabled"]})
emit("Restore Shipping", {"http": status, "enabled": body.get("enabled")})
