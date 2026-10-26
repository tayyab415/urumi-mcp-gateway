"""The store provisioner: an internal API for the gateway, plus a reconcile loop.

    GET    /stores           every store and its phase
    GET    /stores/{slug}    one store
    PUT    /stores/{slug}    create it, or retry it if it failed (safe to repeat)
    DELETE /stores/{slug}    delete it: Helm release, then the whole namespace

Only the gateway calls this (a NetworkPolicy allows nothing else, and a shared
token is checked too). The gateway decides who may create or delete; this
service only checks the platform limits, because it is the one that can count
what exists.

The API only writes a store's record on its namespace. The reconcile loop does
the work: every few seconds, and right after a request, it looks at each store
namespace and moves it one step on. Each step is safe to repeat, so a
provisioner restarted halfway resumes where it was.

Run locally against a kubeconfig:
    PROVISIONER_TOKEN=dev-token-0123456789 STORE_DOMAIN=127-0-0-1.sslip.io MCP_IMAGE_TAG=dev \
    CHART_DIR=../../charts/urumi-store uvicorn provisioner.app:app --port 8300
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Any

import anyio
from anyio import to_thread
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from kubernetes import client

from provisioner import cluster, state
from provisioner.config import Settings

log = logging.getLogger("provisioner")


class StoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    owner_id: int
    owner_email: str = Field(max_length=255)


def store_out(ns: client.V1Namespace, settings: Settings) -> dict[str, Any]:
    labels = ns.metadata.labels or {}
    ann = ns.metadata.annotations or {}
    slug = labels[state.LABEL_SLUG]
    terminating = ns.status is not None and ns.status.phase == "Terminating"
    return {
        "slug": slug,
        "name": ann.get(state.ANN_NAME, slug),
        "namespace": ns.metadata.name,
        "owner_id": int(labels.get(state.LABEL_OWNER, "0")),
        "owner_email": ann.get(state.ANN_OWNER_EMAIL, ""),
        "phase": "Deleting" if terminating else ann.get(state.ANN_PHASE, "Provisioning"),
        "reason": ann.get(state.ANN_REASON, ""),
        "url": f"http://{slug}.{settings.store_domain}",
        "admin_url": f"http://{slug}.{settings.store_domain}/wp-admin/",
        "mcp_url": f"http://{slug}-woo-mcp.{ns.metadata.name}.svc.cluster.local:8000/mcp",
        "created_at": ann.get(state.ANN_CREATED),
        "ready_at": ann.get(state.ANN_READY_AT),
        "deadline": ann.get(state.ANN_DEADLINE),
    }


def caller(request: Request) -> None:
    """Only the gateway holds this token."""
    auth = request.headers.get("authorization", "")
    if not hmac.compare_digest(auth.encode(), f"Bearer {request.app.state.settings.api_token}".encode()):
        raise HTTPException(401, "Not authorized")


Caller = Annotated[None, Depends(caller)]


class Provisioner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.busy: set[str] = set()  # slugs with a reconcile step running
        self.installed: set[str] = set()  # slugs whose Helm release is known to be deployed
        self.create_lock = anyio.Lock()  # limit checks and namespace creation, one at a time
        self.kick = anyio.Event()

    def wake(self) -> None:
        self.kick.set()

    # -------------------------------------------------------------- the loop

    async def run(self) -> None:
        while True:
            try:
                await self.reconcile_all()
            except Exception:
                log.exception("reconcile pass failed")
            with anyio.move_on_after(self.settings.reconcile_interval_seconds):
                await self.kick.wait()
            self.kick = anyio.Event()

    async def reconcile_all(self) -> None:
        namespaces = await to_thread.run_sync(cluster.list_store_namespaces)
        async with anyio.create_task_group() as tg:
            for ns in namespaces:
                slug = (ns.metadata.labels or {}).get(state.LABEL_SLUG)
                if slug and slug not in self.busy:
                    self.busy.add(slug)
                    tg.start_soon(self._reconcile_guarded, ns, slug)

    async def _reconcile_guarded(self, ns: client.V1Namespace, slug: str) -> None:
        try:
            await self.reconcile(ns, slug)
        except Exception as exc:  # noqa: BLE001 - one store's trouble must not stop the others
            log.exception("reconcile %s failed", slug)
            await self._annotate(ns.metadata.name, {state.ANN_REASON: f"Provisioner error, retrying: {cluster.short_error(exc)}"})
        finally:
            self.busy.discard(slug)

    async def _annotate(self, namespace: str, values: dict[str, str]) -> None:
        try:
            await to_thread.run_sync(cluster.annotate, namespace, values)
        except Exception:  # noqa: BLE001
            log.exception("could not annotate %s", namespace)

    async def reconcile(self, ns: client.V1Namespace, slug: str) -> None:
        namespace = ns.metadata.name
        ann = ns.metadata.annotations or {}
        if ns.status is not None and ns.status.phase == "Terminating":
            return  # Kubernetes is already removing everything in it
        phase: state.Phase = ann.get(state.ANN_PHASE, "Provisioning")  # type: ignore[assignment]

        if phase == "Deleting":
            self.installed.discard(slug)
            # Uninstall first so Deployments stop cleanly, then remove the namespace,
            # which takes the volumes, the Secret and the Helm record with it.
            result = await cluster.helm_uninstall(namespace, slug)
            if not result.ok:
                log.warning("helm uninstall %s: %s", slug, result.output)
            await to_thread.run_sync(cluster.delete_namespace, namespace)
            log.info("deleting store %s", slug)
            return

        if phase == "Provisioning":
            if not await self._ensure_release(namespace, slug, ann):
                return
            seen = await to_thread.run_sync(cluster.observe, namespace, slug)
            new_phase, reason = state.next_phase(phase, state.now_utc(), state.parse_iso(ann.get(state.ANN_DEADLINE)), seen)
            update = {state.ANN_PHASE: new_phase, state.ANN_REASON: reason}
            if new_phase == "Ready":
                update[state.ANN_READY_AT] = state.iso(state.now_utc())
                log.info("store %s is ready", slug)
            elif new_phase == "Failed":
                log.warning("store %s failed: %s", slug, reason)
            if update[state.ANN_PHASE] != phase or update[state.ANN_REASON] != ann.get(state.ANN_REASON, ""):
                await self._annotate(namespace, update)
            return

        if phase == "Ready":
            seen = await to_thread.run_sync(cluster.observe, namespace, slug)
            _, reason = state.next_phase(phase, state.now_utc(), None, seen)
            if reason != ann.get(state.ANN_REASON, ""):
                await self._annotate(namespace, {state.ANN_REASON: reason})

    async def _ensure_release(self, namespace: str, slug: str, ann: dict[str, str]) -> bool:
        """Make sure the store's Helm release is installed. False means "not yet"."""
        await to_thread.run_sync(cluster.ensure_rolebinding, namespace, self.settings)
        await to_thread.run_sync(cluster.ensure_secret, namespace)
        if slug in self.installed:
            return True

        status = await cluster.helm_status(namespace, slug)
        helm_state = ((status or {}).get("info") or {}).get("status")
        if helm_state == "deployed":
            self.installed.add(slug)
            return True
        if helm_state and helm_state.startswith("pending"):
            # Only this process runs Helm, one step per store at a time, so a pending
            # release means a provisioner died mid-install. Clear it and try again.
            log.warning("store %s: Helm release left %s; uninstalling before retrying", slug, helm_state)
            await cluster.helm_uninstall(namespace, slug)

        attempts = int(ann.get(state.ANN_ATTEMPTS, "0"))
        if attempts >= self.settings.max_helm_attempts:
            await self._annotate(namespace, {
                state.ANN_PHASE: "Failed",
                state.ANN_REASON: f"Helm install failed {attempts} times. Last error: {ann.get(state.ANN_REASON, '')[:400]}",
            })
            return False
        await self._annotate(namespace, {state.ANN_ATTEMPTS: str(attempts + 1)})
        result = await cluster.helm_install(slug, ann.get(state.ANN_NAME, slug), self.settings)
        if not result.ok:
            await self._annotate(namespace, {state.ANN_REASON: f"Helm: {result.output[-400:]}"})
            return False
        self.installed.add(slug)
        log.info("store %s: Helm release installed", slug)
        return True

    # --------------------------------------------------------------- the API

    async def create(self, slug: str, body: StoreIn) -> tuple[int, dict[str, Any]]:
        if problem := state.check_slug(slug):
            raise HTTPException(422, problem)
        namespace = state.namespace_for(slug)
        async with self.create_lock:
            existing = await to_thread.run_sync(cluster.read_namespace, namespace)
            if existing is not None:
                return self._repeat_create(existing, slug, body)

            stores = [store_out(ns, self.settings) for ns in await to_thread.run_sync(cluster.list_store_namespaces)]
            active = [s for s in stores if s["phase"] != "Deleting"]
            mine = [s for s in active if s["owner_id"] == body.owner_id]
            if len(mine) >= self.settings.max_stores_per_owner:
                raise HTTPException(403, f"You already have {len(mine)} stores, the most one person may have. Delete one first.")
            if len(active) >= self.settings.max_stores_total:
                raise HTTPException(503, f"The platform already runs {len(active)} stores, its limit. Try again after one is deleted.")

            now = state.now_utc()
            created = await to_thread.run_sync(cluster.create_namespace, slug, {
                state.LABEL_STORE: "true",
                state.LABEL_SLUG: slug,
                state.LABEL_OWNER: str(body.owner_id),
            }, {
                state.ANN_NAME: body.name,
                state.ANN_OWNER_EMAIL: body.owner_email,
                state.ANN_PHASE: "Provisioning",
                state.ANN_REASON: "Starting",
                state.ANN_CREATED: state.iso(now),
                state.ANN_DEADLINE: state.iso(now + timedelta(seconds=self.settings.provision_timeout_seconds)),
                state.ANN_ATTEMPTS: "0",
            })
        log.info("store %s requested by user %s", slug, body.owner_id)
        self.wake()
        return 201, store_out(created, self.settings)

    def _repeat_create(self, existing: client.V1Namespace, slug: str, body: StoreIn) -> tuple[int, dict[str, Any]]:
        labels = existing.metadata.labels or {}
        if labels.get(state.LABEL_STORE) != "true":
            raise HTTPException(409, f"The name {slug!r} is taken by something that is not a store.")
        if labels.get(state.LABEL_OWNER) != str(body.owner_id):
            raise HTTPException(409, f"Someone else already has a store called {slug!r}.")
        current = store_out(existing, self.settings)
        if current["phase"] == "Deleting":
            raise HTTPException(409, f"{slug!r} is being deleted. Try again when it is gone.")
        if current["phase"] == "Failed":
            # Retrying a failed store: same namespace, same Secret, a fresh deadline.
            now = state.now_utc()
            cluster.annotate(existing.metadata.name, {
                state.ANN_PHASE: "Provisioning",
                state.ANN_REASON: "Retrying",
                state.ANN_DEADLINE: state.iso(now + timedelta(seconds=self.settings.provision_timeout_seconds)),
                state.ANN_ATTEMPTS: "0",
            })
            self.installed.discard(slug)
            self.wake()
            current.update(phase="Provisioning", reason="Retrying")
        return 200, current

    async def delete(self, slug: str) -> dict[str, Any]:
        namespace = state.namespace_for(slug)
        existing = await to_thread.run_sync(cluster.read_namespace, namespace)
        if existing is None or (existing.metadata.labels or {}).get(state.LABEL_STORE) != "true":
            raise HTTPException(404, f"No store called {slug!r}.")
        await to_thread.run_sync(cluster.annotate, namespace, {state.ANN_PHASE: "Deleting", state.ANN_REASON: "Deleting"})
        self.wake()
        out = store_out(existing, self.settings)
        out.update(phase="Deleting", reason="Deleting")
        return out


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cluster.load_config()
    prov = Provisioner(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with anyio.create_task_group() as tg:
            tg.start_soon(prov.run)
            yield
            tg.cancel_scope.cancel()

    app = FastAPI(title="Urumi store provisioner", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        # Ready when the Kubernetes API answers: without it nothing here works.
        with anyio.fail_after(5):
            await to_thread.run_sync(cluster.list_store_namespaces)
        return {"status": "ok"}

    @app.get("/stores")
    async def list_stores(_: Caller) -> list[dict[str, Any]]:
        namespaces = await to_thread.run_sync(cluster.list_store_namespaces)
        return sorted((store_out(ns, settings) for ns in namespaces), key=lambda s: s["created_at"] or "")

    @app.get("/stores/{slug}")
    async def get_store(slug: str, _: Caller) -> dict[str, Any]:
        ns = await to_thread.run_sync(cluster.read_namespace, state.namespace_for(slug))
        if ns is None or (ns.metadata.labels or {}).get(state.LABEL_STORE) != "true":
            raise HTTPException(404, f"No store called {slug!r}.")
        return store_out(ns, settings)

    @app.put("/stores/{slug}")
    async def put_store(slug: str, body: StoreIn, _: Caller) -> Any:
        status, out = await prov.create(slug, body)
        return JSONResponse(out, status_code=status)

    @app.delete("/stores/{slug}", status_code=202)
    async def delete_store(slug: str, _: Caller) -> dict[str, Any]:
        return await prov.delete(slug)

    app.state.provisioner = prov
    return app


def __getattr__(name: str) -> Any:
    if name == "app":
        return create_app()
    raise AttributeError(name)
