"""Runtime settings, read once from environment variables.

In Kubernetes, the secret ones come from a Secret through `env.valueFrom`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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
        )
