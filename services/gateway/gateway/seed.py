"""Create the team and register the upstream servers, through the gateway's own HTTP API.

    python -m gateway.seed --url http://localhost:8080 --config seed/team.local.json \
        --credentials-out ~/.urumi/team-credentials.json --keys

It uses only the public API, so the same command works against a gateway on your
laptop and as a Kubernetes Job pointed at the gateway Service. It never touches the
database file, which matters while the database is a SQLite file the gateway owns.

Safe to run again: people and servers that already exist are left alone. A person
who already exists keeps their password, and the seeder cannot make them an API key.

The admin signs in with BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD, the same
Secret values the gateway reads on first start.

Passwords for new people are random unless SEED_USER_PASSWORD is set. They are
written to --credentials-out (mode 600) and nowhere else, so that file is required
whenever someone new is created.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2

CSRF = {"X-Urumi-CSRF": "1"}
ENV_REF = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


class SeedError(Exception):
    pass


def expand(value: str, env: dict[str, str]) -> str:
    """Replace ${NAME} and ${NAME:-default} with environment values."""

    def one(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        if name in env and env[name] != "":
            return env[name]
        if default is not None:
            return default
        raise SeedError(f"environment variable {name} is not set")

    return ENV_REF.sub(one, value)


def check(r: httpx2.Response, what: str) -> Any:
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise SeedError(f"{what} failed (HTTP {r.status_code}): {detail}")
    return r.json()


async def seed(
    config: dict[str, Any],
    new_client: Callable[[], httpx2.AsyncClient],
    admin_email: str,
    admin_password: str,
    env: dict[str, str],
    make_keys: bool = False,
) -> dict[str, Any]:
    """Returns {"credentials": {email: {...}}, "report": [lines]}. Prints nothing."""
    report: list[str] = []
    credentials: dict[str, dict[str, str]] = {}
    shared_password = env.get("SEED_USER_PASSWORD") or None

    async with new_client() as admin:
        check(await admin.post("/api/auth/login", json={"email": admin_email, "password": admin_password}), "admin login")

        have_users = {u["email"] for u in check(await admin.get("/api/users"), "list users")}
        for person in config.get("users", []):
            email = person["email"].strip().lower()
            if email in have_users:
                report.append(f"user     {email:<24} already exists")
                continue
            invite = check(await admin.post("/api/invites", json={"email": email, "role": person.get("role", "member")}), f"invite {email}")
            token = invite["invite_url"].split("token=", 1)[1]
            password = shared_password or secrets.token_urlsafe(14)
            async with new_client() as session:  # the new person's own cookie jar
                check(await session.post(f"/api/invites/accept/{token}", json={"name": person["name"], "password": password}), f"accept {email}")
                entry = {"password": password, "role": person.get("role", "member")}
                if make_keys:
                    entry["api_key"] = check(await session.post("/api/keys", json={"name": f"{person['name']} seed key"}), f"key for {email}")["key"]
            credentials[email] = entry
            report.append(f"user     {email:<24} created as {entry['role']}" + (", with an API key" if make_keys else ""))

        have_servers = {s["slug"] for s in check(await admin.get("/api/servers"), "list servers")}
        for server in config.get("servers", []):
            if server["slug"] in have_servers:
                report.append(f"server   {server['slug']:<24} already registered")
                continue
            body = {k: v for k, v in server.items() if k not in ("credential_env", "url")}
            body["url"] = expand(server["url"], env)
            if server.get("credential_env"):
                if not env.get(server["credential_env"]):
                    raise SeedError(f"server {server['slug']} needs ${server['credential_env']}, which is not set")
                body["credential"] = env[server["credential_env"]]
            created = check(await admin.post("/api/servers", json=body), f"register {server['slug']}")
            report.append(f"server   {server['slug']:<24} registered, status {created['status']}, {created['tool_count']} tools")

    return {"credentials": credentials, "report": report}


def write_credentials(path: Path, new: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    merged: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    merged.update(new)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(merged, handle, indent=2)
    os.chmod(path, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="gateway base URL, e.g. http://localhost:8080")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--credentials-out", type=Path, help="where to write passwords and keys of new people (mode 600)")
    parser.add_argument("--keys", action="store_true", help="also make one API key for each new person")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    try:
        config = json.loads(args.config.read_text())
        email, password = env.get("BOOTSTRAP_ADMIN_EMAIL"), env.get("BOOTSTRAP_ADMIN_PASSWORD")
        if not (email and password):
            raise SeedError("set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD")
        if config.get("users") and not args.credentials_out:
            raise SeedError("--credentials-out is required, because new people get passwords that must be saved somewhere")
        result = asyncio.run(
            seed(config, lambda: httpx2.AsyncClient(base_url=args.url.rstrip("/"), headers=CSRF, timeout=120), email, password, env, args.keys)
        )
    except SeedError as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 1
    if result["credentials"]:
        write_credentials(args.credentials_out, result["credentials"])
    print("\n".join(result["report"]))
    if result["credentials"]:
        print(f"saved passwords{' and keys' if args.keys else ''} for {len(result['credentials'])} new people to {args.credentials_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
