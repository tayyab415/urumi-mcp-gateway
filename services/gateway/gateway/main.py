"""Assembles the gateway process.

    /mcp       the MCP endpoint for external clients (API key)
    /api/...   the dashboard API (login session)
    /healthz   liveness: the process is serving
    /readyz    readiness: the process can reach its own database

Readiness deliberately ignores upstream health. One dead MCP server should make
its own tools fail, not take the whole gateway out of the Service.

Run locally:
    GATEWAY_SECRET_KEY=dev-secret-change-me BOOTSTRAP_ADMIN_EMAIL=admin@example.com \
    BOOTSTRAP_ADMIN_PASSWORD=change-me-now ALLOW_LOOPBACK_UPSTREAMS=true \
    uvicorn gateway.main:app --port 8080
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from gateway import api, stores
from gateway.config import Settings
from gateway.core import Gateway
from gateway.db import Database, Server, User
from gateway.mcp_endpoint import mcp_asgi_app
from gateway.security import ChatTokens, CredentialBox, hash_password

log = logging.getLogger("gateway")


async def bootstrap_admin(db: Database, settings: Settings) -> None:
    """Create the first admin from the Secret, once, when there are no users at all."""
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    async with db.session() as s:
        if await s.scalar(select(func.count()).select_from(User)):
            return
        s.add(User(email=settings.bootstrap_admin_email.lower(), name="Admin", role="admin",
                   password_hash=hash_password(settings.bootstrap_admin_password)))
        await s.commit()
        log.info("created bootstrap admin %s", settings.bootstrap_admin_email)


async def bootstrap_servers(db: Database, gateway: Gateway, settings: Settings) -> None:
    """Register the servers in BOOTSTRAP_SERVERS_FILE, once, on a gateway with no servers.

    This is how a fresh `helm install` shows WooCommerce as the first server. After
    the first boot the database is the source of truth: a server an admin removes
    stays removed, and edits are not overwritten on restart. A bad entry stops
    startup, since it is a deployment mistake, not something to skip silently.

    The file is a ConfigMap, so it holds no secrets. An entry names the environment
    variable with its credential (`credential_env`), and that variable comes from
    the Secret.
    """
    if not settings.bootstrap_servers_file:
        return
    raw = json.loads(Path(settings.bootstrap_servers_file).read_text())
    for item in raw:
        if name := item.pop("credential_env", None):
            if not os.environ.get(name):
                raise RuntimeError(f"{name} is not set; it holds the credential for bootstrap server {item.get('slug')!r}")
            item["credential"] = os.environ[name]
    entries = [api.ServerIn.model_validate(item) for item in raw]
    for entry in entries:
        await api.check_upstream_url(entry.url, settings.allow_loopback_upstreams)
        api.credential_rules(entry.auth_type, entry.auth_header)
    async with db.session() as s:
        if await s.scalar(select(func.count()).select_from(Server)):
            return
        s.add_all(api.new_server(entry, gateway.box) for entry in entries)
        await s.commit()
    log.info("registered bootstrap servers: %s", ", ".join(e.slug for e in entries))


async def refresh_forever(gateway: Gateway, interval: float) -> None:
    while True:
        try:
            await gateway.refresh_all()
        except Exception:
            log.exception("catalog refresh failed")
        await anyio.sleep(interval)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # it logs every upstream URL

    db = Database(settings.database_url)
    gateway = Gateway(db, CredentialBox(settings.secret_key))
    chat_tokens = ChatTokens(settings.secret_key)
    mcp_app, mcp_manager = mcp_asgi_app(gateway, db, chat_tokens)
    provisioner = (
        stores.Provisioner(settings.provisioner_url, settings.provisioner_token)
        if settings.provisioner_url and settings.provisioner_token else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await db.create_all()
        await bootstrap_admin(db, settings)
        await bootstrap_servers(db, gateway, settings)
        async with mcp_manager.run(), anyio.create_task_group() as tg:
            if settings.refresh_interval_seconds > 0:
                tg.start_soon(refresh_forever, gateway, settings.refresh_interval_seconds)
            if provisioner is not None:
                tg.start_soon(stores.sync_forever, db, gateway, provisioner, settings.store_sync_interval_seconds)
            yield
            tg.cancel_scope.cancel()
        await db.dispose()

    app = FastAPI(title="Urumi MCP gateway", version="0.1.0", lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.state.settings, app.state.db, app.state.gateway = settings, db, gateway
    app.state.chat_tokens = chat_tokens
    app.state.provisioner = provisioner

    app.add_route("/mcp", mcp_app, methods=["GET", "POST", "DELETE"], include_in_schema=False)
    app.add_route("/mcp/", mcp_app, methods=["GET", "POST", "DELETE"], include_in_schema=False)
    api.install(app, settings)
    app.include_router(stores.build_router())

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> Any:
        try:
            with anyio.fail_after(3):
                async with db.session() as s:
                    await s.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"status": "unavailable", "reason": exc.__class__.__name__}, status_code=503)
        return {"status": "ok"}

    return app


def __getattr__(name: str) -> Any:
    # `uvicorn gateway.main:app` builds the app on first access; tests call create_app().
    if name == "app":
        return create_app()
    raise AttributeError(name)
