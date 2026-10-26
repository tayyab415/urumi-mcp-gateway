"""Register the local MCP servers with the dev gateway (idempotent).

The Slack server is added only when ~/.urumi/slack.env exists, the same test dev-stack.sh uses.
"""

from pathlib import Path

import httpx2

GW = "http://127.0.0.1:8080"
SERVERS = [
    {"slug": "woo", "name": "WooCommerce", "url": "http://127.0.0.1:18912/mcp", "timeout_seconds": 15},
    {"slug": "shipping", "name": "Shipping (simulated)", "url": "http://127.0.0.1:18921/mcp",
     "auth_type": "bearer", "credential": "dev-shipping-token", "timeout_seconds": 5},
    {"slug": "supplier", "name": "Supplier (simulated)", "url": "http://127.0.0.1:18922/mcp",
     "auth_type": "header", "auth_header": "X-Supplier-Key", "credential": "dev-supplier-key", "timeout_seconds": 5},
]
if (Path.home() / ".urumi" / "slack.env").exists():
    SERVERS.append({"slug": "slack", "name": "Slack (one channel)", "url": "http://127.0.0.1:18931/mcp",
                    "auth_type": "bearer", "credential": "dev-slack-token", "timeout_seconds": 10, "forward_identity": True})

with httpx2.Client(base_url=GW, headers={"X-Urumi-CSRF": "1"}) as http:
    http.post("/api/auth/login", json={"email": "admin@urumi.test", "password": "admin-password-1"}).raise_for_status()
    existing = {s["slug"] for s in http.get("/api/servers").json()}
    for server in SERVERS:
        if server["slug"] not in existing:
            r = http.post("/api/servers", json=server)
            r.raise_for_status()
    for s in http.get("/api/servers").json():
        print(f"{s['slug']:10} {s['status']:12} tools={s['tool_count']}  {s['url']}")
    print("\nDashboard API: http://localhost:8080/api/docs   MCP endpoint: http://localhost:8080/mcp")
    print("Login: admin@urumi.test / admin-password-1  (create an API key under /api/keys)")
