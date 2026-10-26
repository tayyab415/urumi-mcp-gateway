import asyncio
import collections
import json
import sys
import time

import httpx2

x = json.load(sys.stdin)


async def main():
    async with httpx2.AsyncClient(
        timeout=20,
        limits=httpx2.Limits(max_connections=100, max_keepalive_connections=100),
    ) as http:
        async def call(auth):
            response = await http.post(
                x["base"] + "/mcp",
                headers={"Authorization": auth, "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            return response.status_code

        for label, headers in [
            ("100 requests, one key and identical header", ["Bearer " + x["fixed"]] * 100),
            (
                "100 requests, one key and different whitespace in header",
                ["Bearer " + " " * i + x["variant"] for i in range(1, 101)],
            ),
        ]:
            started = time.monotonic()
            statuses = await asyncio.gather(*(call(header) for header in headers))
            print(label, dict(collections.Counter(statuses)), "seconds", round(time.monotonic() - started, 3))


asyncio.run(main())
