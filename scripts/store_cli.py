"""Create, list and delete provisioned stores through the gateway, as one team member.

    cd services/gateway && uv run python ../../scripts/store_cli.py --as ana list
    ... --as ana create north "North Sports"
    ... --as ana wait north          # until Ready or Failed, printing each change
    ... --as ana delete north

Signs in with the member's password (the dashboard's API, not /mcp), so it sees
exactly what the Stores page shows. Defaults to the VM's public URL and
~/.urumi/vm-team-credentials.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx2

CREDS = Path.home() / ".urumi" / "vm-team-credentials.json"
ADMIN_SECRET = Path(__file__).resolve().parent.parent / ".runtime/app/secrets.json"


def password_for(email: str, creds: Path) -> str:
    if email == "admin@urumi.test":
        return json.loads(ADMIN_SECRET.read_text())["stringData"]["admin-password"]
    return json.loads(creds.read_text())[email]["password"]


def show(s: dict) -> str:
    reason = f"  {s['reason']}" if s.get("reason") else ""
    tools = f" tools={s['server_slug']}__* ({s['server_status']})" if s.get("registered") else ""
    return f"{s['slug']:<10} {s['phase']:<12} {s['url']:<40} owner={s['owner_email']}{tools}{reason}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--as", dest="who", required=True)
    p.add_argument("--base", default="https://app.34-67-189-86.sslip.io")
    p.add_argument("--creds", type=Path, default=CREDS)
    p.add_argument("cmd", choices=["list", "create", "wait", "delete"])
    p.add_argument("rest", nargs="*")
    args = p.parse_args()
    email = args.who if "@" in args.who else f"{args.who}@urumi.test"
    with httpx2.Client(base_url=args.base, headers={"X-Urumi-CSRF": "1"}, timeout=60) as http:
        r = http.post("/api/auth/login", json={"email": email, "password": password_for(email, args.creds)})
        r.raise_for_status()
        if args.cmd == "list":
            for s in http.get("/api/stores").json()["stores"]:
                print(show(s))
            return 0
        if args.cmd == "create":
            r = http.post("/api/stores", json={"slug": args.rest[0], "name": args.rest[1] if len(args.rest) > 1 else args.rest[0]})
            print(f"HTTP {r.status_code}: {show(r.json()) if r.is_success else r.json()['detail']}")
            return 0 if r.is_success else 1
        if args.cmd == "delete":
            r = http.delete(f"/api/stores/{args.rest[0]}")
            print(f"HTTP {r.status_code}: {show(r.json()) if r.is_success else r.json()['detail']}")
            return 0 if r.is_success else 1
        started, last = time.monotonic(), None
        while True:
            store = next((s for s in http.get("/api/stores").json()["stores"] if s["slug"] == args.rest[0]), None)
            line = show(store) if store else f"{args.rest[0]} is gone"
            if line != last:
                print(f"[{time.monotonic() - started:6.0f}s] {line}", flush=True)
                last = line
            if store is None or (store["phase"] in ("Ready", "Failed") and (store["registered"] or store["phase"] == "Failed")):
                return 0
            time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
