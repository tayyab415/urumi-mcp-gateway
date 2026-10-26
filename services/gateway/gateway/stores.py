"""Stores made by the store provisioner, and their automatic registration as upstreams.

    dashboard ──/api/stores──▶ gateway ──PUT /stores/{slug}──▶ provisioner ──Helm──▶ namespace store-<slug>
                                  ▲                                                       │
                                  └──────── sync loop: Ready store ⇒ server "store-<slug>" ┘

The gateway decides who may create or delete a store (any signed-in person may
create one; only its owner or an admin may delete it). The provisioner does the
Kubernetes work and enforces the platform limits, since it is the one that can
count what exists.

Registration is a reconcile loop, not a callback: every few seconds the gateway
asks the provisioner for the stores, registers the MCP server of each Ready
store as `store-<slug>`, and removes registrations whose store is gone. A
gateway or provisioner restart loses nothing; the next pass catches up.
Deleting a store removes its registration first, so its tools disappear from
every client before the namespace starts to go.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import anyio
import httpx2
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from gateway.api import current_user
from gateway.core import Gateway
from gateway.db import Database, Server, User

log = logging.getLogger("gateway.stores")

MANAGED_BY = "provisioner"
SERVER_PREFIX = "store-"


def server_slug(store_slug: str) -> str:
    return f"{SERVER_PREFIX}{store_slug}"


class ProvisionerError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Provisioner:
    """The gateway's client for the provisioner's internal API."""

    def __init__(self, url: str, token: str, timeout: float = 15):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.timeout = timeout

    async def _call(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx2.AsyncClient(timeout=self.timeout, headers=self.headers) as http:
                response = await http.request(method, f"{self.url}{path}", json=json)
        except httpx2.HTTPError as exc:
            raise ProvisionerError(503, f"The store provisioner is unreachable ({exc.__class__.__name__}).") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise ProvisionerError(response.status_code, str(detail or f"Provisioner answered HTTP {response.status_code}"))
        return response.json()

    async def list(self) -> list[dict[str, Any]]:
        return await self._call("GET", "/stores")

    async def put(self, slug: str, name: str, owner: User) -> dict[str, Any]:
        return await self._call("PUT", f"/stores/{slug}", {"name": name, "owner_id": owner.id, "owner_email": owner.email})

    async def delete(self, slug: str) -> dict[str, Any]:
        return await self._call("DELETE", f"/stores/{slug}")


async def sync_registrations(db: Database, gateway: Gateway, provisioner: Provisioner) -> None:
    """Make the registered store servers match the stores that are Ready."""
    stores = await provisioner.list()
    ready = {server_slug(s["slug"]): s for s in stores if s["phase"] == "Ready"}
    added: list[int] = []
    async with db.session() as s:
        managed = {x.slug: x for x in await s.scalars(select(Server).where(Server.managed_by == MANAGED_BY))}
        for slug, server in managed.items():
            if slug not in ready:
                log.info("store server %s: store gone or not ready; removing its registration", slug)
                await s.delete(server)
        for slug, store in ready.items():
            if slug in managed:
                continue
            if await s.scalar(select(Server).where(Server.slug == slug)):
                log.warning("store server %s: slug taken by a server an admin added; not registering", slug)
                continue
            server = Server(
                slug=slug, name=f"Store: {store['name']}", url=store["mcp_url"], enabled=True,
                auth_type="none", timeout_seconds=15, managed_by=MANAGED_BY, owner_id=store["owner_id"],
            )
            s.add(server)
            await s.flush()
            added.append(server.id)
            log.info("store server %s: registered %s", slug, store["mcp_url"])
        await s.commit()
    for server_id in added:
        await gateway.refresh_server(server_id)  # discover its tools now, not on the next refresh


async def sync_forever(db: Database, gateway: Gateway, provisioner: Provisioner, interval: float) -> None:
    while True:
        try:
            await sync_registrations(db, gateway, provisioner)
        except ProvisionerError as exc:
            log.warning("store sync: %s", exc.detail)
        except Exception:
            log.exception("store sync failed")
        await anyio.sleep(interval)


class StoreIn(BaseModel):
    slug: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=1, max_length=40)


CurrentUser = Annotated[User, Depends(current_user)]


def build_router() -> APIRouter:
    r = APIRouter(prefix="/api/stores")

    def provisioner_of(request: Request) -> Provisioner:
        prov = getattr(request.app.state, "provisioner", None)
        if prov is None:
            raise HTTPException(404, "Store provisioning is not enabled on this gateway")
        return prov

    async def decorate(request: Request, stores: list[dict[str, Any]], user: User) -> list[dict[str, Any]]:
        async with request.app.state.db.session() as s:
            registered = {x.slug: x for x in await s.scalars(select(Server).where(Server.managed_by == MANAGED_BY))}
        for store in stores:
            server = registered.get(server_slug(store["slug"]))
            store["server_slug"] = server_slug(store["slug"])
            store["registered"] = server is not None
            store["server_status"] = None if server is None else ("disabled" if not server.enabled else server.status)
            store["mine"] = store["owner_id"] == user.id
            store["can_delete"] = store["mine"] or user.role == "admin"
        return stores

    @r.get("")
    async def list_stores(user: CurrentUser, request: Request) -> dict[str, Any]:
        prov = provisioner_of(request)
        try:
            stores = await prov.list()
        except ProvisionerError as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        return {"stores": await decorate(request, stores, user)}

    @r.post("")
    async def create_store(body: StoreIn, user: CurrentUser, request: Request) -> Any:
        prov = provisioner_of(request)
        try:
            store = await prov.put(body.slug, body.name, user)
        except ProvisionerError as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        log.info("store %s requested by %s", body.slug, user.email)
        return (await decorate(request, [store], user))[0]

    @r.delete("/{slug}")
    async def delete_store(slug: str, user: CurrentUser, request: Request) -> dict[str, Any]:
        prov = provisioner_of(request)
        try:
            stores = {s["slug"]: s for s in await prov.list()}
        except ProvisionerError as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        store = stores.get(slug)
        if store is None:
            raise HTTPException(404, f"No store called {slug!r}")
        if store["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Only the person who created a store, or an admin, can delete it")
        try:
            out = await prov.delete(slug)  # now marked Deleting, so the sync loop will not re-register it
        except ProvisionerError as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        # The registration goes now, before the namespace does, so no client can
        # call into a store that is being torn down.
        async with request.app.state.db.session() as s:
            server = await s.scalar(select(Server).where(Server.slug == server_slug(slug), Server.managed_by == MANAGED_BY))
            if server is not None:
                await s.delete(server)
                await s.commit()
        log.info("store %s deleted by %s", slug, user.email)
        return (await decorate(request, [out], user))[0]

    return r
