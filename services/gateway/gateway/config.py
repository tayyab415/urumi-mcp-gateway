"""Runtime settings, read once from environment variables.

In Kubernetes, the secret ones come from a Secret through `env.valueFrom`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Encrypts upstream credentials at rest. Changing it makes stored credentials
    # unreadable; admins then re-enter them.
    secret_key: str
    bootstrap_admin_email: str | None
    bootstrap_admin_password: str | None
    # The address people and MCP clients use, with no trailing slash and no path. It is also the OAuth
    # issuer, and `<public_url>/mcp` is the audience of every OAuth token. Browser sign-in for MCP clients
    # needs https (or localhost); on any other http address it switches itself off.
    public_url: str
    cookie_secure: bool
    session_hours: int
    refresh_interval_seconds: float
    default_timeout_seconds: float
    # Local development registers upstreams on 127.0.0.1. In the cluster they are
    # Service names, so loopback stays blocked there.
    allow_loopback_upstreams: bool
    # A JSON list in the shape of POST /api/servers, registered once on a gateway
    # that has no servers yet. In the cluster it is a mounted Secret file.
    bootstrap_servers_file: str | None = None
    # The store provisioner (multi-store bonus). Unset: no Stores API.
    provisioner_url: str | None = None
    provisioner_token: str | None = None
    store_sync_interval_seconds: float = 5.0
    # Host headers /mcp accepts (DNS-rebinding protection). Empty: derived from
    # PUBLIC_URL plus loopback; MCP_ALLOWED_HOSTS adds more, e.g. the in-cluster
    # Service name the chat service uses. "*" turns the check off.
    extra_allowed_hosts: tuple[str, ...] = ()
    # A public demo: the sign-in page lists the people and signs in as one with
    # a click, no password. Anyone who can open the dashboard can then act as
    # anyone, admins included, so it stays off unless a deployment asks for it.
    demo_login: bool = False
    # Shown on the Stores page: the first store's address, and the domain new
    # stores get a name under. The dashboard cannot guess them from its own
    # address, which may be a proxy's (infra/cloud-run).
    store_public_url: str | None = None
    store_domain: str | None = None

    def mcp_allowed_hosts(self) -> list[str]:
        if "*" in self.extra_allowed_hosts:
            return []
        host = urlsplit(self.public_url).netloc
        return [host, f"{host.split(':')[0]}:*", "localhost:*", "127.0.0.1:*", *self.extra_allowed_hosts]

    def mcp_allowed_origins(self) -> list[str]:
        return [self.public_url, "http://localhost:*", "http://127.0.0.1:*"]

    @classmethod
    def from_env(cls) -> Settings:
        secret_key = os.environ.get("GATEWAY_SECRET_KEY", "")
        if len(secret_key) < 16:
            raise RuntimeError("GATEWAY_SECRET_KEY must be set to a random string of at least 16 characters")
        return cls(
            database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./gateway.db"),
            secret_key=secret_key,
            bootstrap_admin_email=os.environ.get("BOOTSTRAP_ADMIN_EMAIL") or None,
            bootstrap_admin_password=os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or None,
            public_url=os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/"),
            cookie_secure=_bool("COOKIE_SECURE", False),
            session_hours=int(os.environ.get("SESSION_HOURS", "12")),
            refresh_interval_seconds=float(os.environ.get("REFRESH_INTERVAL_SECONDS", "30")),
            default_timeout_seconds=float(os.environ.get("DEFAULT_UPSTREAM_TIMEOUT_SECONDS", "10")),
            allow_loopback_upstreams=_bool("ALLOW_LOOPBACK_UPSTREAMS", False),
            bootstrap_servers_file=os.environ.get("BOOTSTRAP_SERVERS_FILE") or None,
            provisioner_url=os.environ.get("PROVISIONER_URL") or None,
            provisioner_token=os.environ.get("PROVISIONER_TOKEN") or None,
            store_sync_interval_seconds=float(os.environ.get("STORE_SYNC_INTERVAL_SECONDS", "5")),
            extra_allowed_hosts=tuple(h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()),
            demo_login=_bool("DEMO_LOGIN", False),
            store_public_url=os.environ.get("STORE_PUBLIC_URL") or None,
            store_domain=os.environ.get("STORE_DOMAIN") or None,
        )
