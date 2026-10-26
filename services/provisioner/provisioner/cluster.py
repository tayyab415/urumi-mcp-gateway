"""Everything the provisioner does to the cluster: the Kubernetes API and Helm.

The Kubernetes client is synchronous; callers run these functions in a worker
thread. Helm runs as a subprocess with the Pod's own ServiceAccount, so it has
exactly the provisioner's permissions and no more.

Every operation here is safe to repeat: creating something that exists is a
no-op, deleting something that is gone is a no-op. That is what makes a retried
request, or a provisioner restarted halfway through, harmless.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from provisioner import state
from provisioner.config import Settings

log = logging.getLogger("provisioner.cluster")

SECRET_NAME = "store-credentials"
ROLEBINDING_NAME = "urumi-provisioner"


def load_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()  # running on a laptop against a kubeconfig


def _core() -> client.CoreV1Api:
    return client.CoreV1Api()


def _missing(exc: ApiException) -> bool:
    return exc.status == 404


def _exists(exc: ApiException) -> bool:
    return exc.status == 409


# ------------------------------------------------------------------ namespaces


def list_store_namespaces() -> list[client.V1Namespace]:
    return _core().list_namespace(label_selector=f"{state.LABEL_STORE}=true").items


def read_namespace(name: str) -> client.V1Namespace | None:
    try:
        return _core().read_namespace(name)
    except ApiException as exc:
        if _missing(exc):
            return None
        raise


def create_namespace(slug: str, labels: dict[str, str], annotations: dict[str, str]) -> client.V1Namespace:
    body = client.V1Namespace(metadata=client.V1ObjectMeta(
        name=state.namespace_for(slug),
        labels={
            **labels,
            # WordPress's Apache starts as root, so these namespaces get the
            # "baseline" standard (no privileged or host access) instead of
            # "restricted", which the app namespace uses.
            "pod-security.kubernetes.io/enforce": "baseline",
            "pod-security.kubernetes.io/warn": "restricted",
        },
        annotations=annotations,
    ))
    return _core().create_namespace(body)


def annotate(name: str, annotations: dict[str, str]) -> None:
    _core().patch_namespace(name, {"metadata": {"annotations": annotations}})


def delete_namespace(name: str) -> None:
    try:
        _core().delete_namespace(name, propagation_policy="Foreground")
    except ApiException as exc:
        if not _missing(exc):
            raise


# ---------------------------------------------------- per-store access + Secret


def ensure_rolebinding(namespace: str, settings: Settings) -> None:
    """Give the provisioner its working permissions inside this one namespace.

    Its cluster-wide role can create namespaces and RoleBindings, nothing else.
    The ClusterRole bound here (`urumi-store-tenant`) holds the Deployment,
    Secret and Job permissions Helm needs, and it only takes effect in a
    namespace that has this binding. An admission policy refuses the binding,
    and every other write by the provisioner, outside store namespaces.
    """
    body = client.V1RoleBinding(
        metadata=client.V1ObjectMeta(name=ROLEBINDING_NAME, namespace=namespace),
        role_ref=client.V1RoleRef(api_group="rbac.authorization.k8s.io", kind="ClusterRole", name=settings.tenant_cluster_role),
        subjects=[client.RbacV1Subject(kind="ServiceAccount", name=settings.service_account, namespace=settings.own_namespace)],
    )
    try:
        client.RbacAuthorizationV1Api().create_namespaced_role_binding(namespace, body)
    except ApiException as exc:
        if not _exists(exc):
            raise


def new_credentials() -> dict[str, str]:
    # The WooCommerce REST key is chosen here; the setup Job registers it in the
    # store and the store's MCP server signs requests with it.
    return {
        "mariadb-root-password": secrets.token_urlsafe(32),
        "wordpress-db-password": secrets.token_urlsafe(32),
        "wordpress-admin-password": secrets.token_urlsafe(24),
        "woo-consumer-key": "ck_" + secrets.token_hex(20),
        "woo-consumer-secret": "cs_" + secrets.token_hex(20),
    }


def ensure_secret(namespace: str) -> None:
    """Create the store's credentials once. A retry keeps the first values."""
    body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=SECRET_NAME, namespace=namespace, labels={"app.kubernetes.io/part-of": "urumi-store"}),
        type="Opaque",
        string_data=new_credentials(),
    )
    try:
        _core().create_namespaced_secret(namespace, body)
    except ApiException as exc:
        if not _exists(exc):
            raise


# ------------------------------------------------------------------ observing


def _pod_problems(namespace: str) -> dict[str, str]:
    """Why each not-ready Pod is not ready, keyed by its component label."""
    problems: dict[str, str] = {}
    for pod in _core().list_namespaced_pod(namespace).items:
        component = (pod.metadata.labels or {}).get("app.kubernetes.io/component", pod.metadata.name)
        if pod.status.phase == "Succeeded":
            continue
        reason = None
        for cond in pod.status.conditions or []:
            if cond.type == "PodScheduled" and cond.status == "False":
                reason = f"not scheduled ({cond.message or cond.reason})"
        for cs in (pod.status.init_container_statuses or []) + (pod.status.container_statuses or []):
            waiting = cs.state.waiting if cs.state else None
            if waiting and waiting.reason not in (None, "ContainerCreating", "PodInitializing"):
                reason = f"{waiting.reason}" + (f": {waiting.message[:160]}" if waiting.message else "")
            elif cs.state and cs.state.terminated and cs.state.terminated.exit_code:
                reason = f"exited with code {cs.state.terminated.exit_code} ({cs.state.terminated.reason})"
        if reason:
            problems[component] = reason
    return problems


def _deployment_ready(namespace: str, name: str) -> tuple[bool, str | None]:
    try:
        d = client.AppsV1Api().read_namespaced_deployment_status(name, namespace)
    except ApiException as exc:
        if _missing(exc):
            return False, None
        raise
    for cond in d.status.conditions or []:
        if cond.type == "ReplicaFailure" and cond.status == "True":
            return False, cond.message  # e.g. refused by the namespace's ResourceQuota
    return bool(d.status.available_replicas), None


def observe(namespace: str, release: str) -> state.Observed:
    seen = state.Observed()
    problems = _pod_problems(namespace)

    try:
        sts = client.AppsV1Api().read_namespaced_stateful_set_status(f"{release}-mariadb", namespace)
        seen.database_ready = bool(sts.status.ready_replicas)
    except ApiException as exc:
        if not _missing(exc):
            raise
    seen.wordpress_ready, wp_problem = _deployment_ready(namespace, f"{release}-wordpress")
    seen.mcp_ready, mcp_problem = _deployment_ready(namespace, f"{release}-woo-mcp")

    try:
        job = client.BatchV1Api().read_namespaced_job_status(f"{release}-store-setup", namespace)
        seen.setup_done = bool(job.status.succeeded)
        for cond in job.status.conditions or []:
            if cond.type == "Failed" and cond.status == "True":
                seen.setup_failed = f"{cond.reason}: {cond.message}" if cond.message else cond.reason
    except ApiException as exc:
        if not _missing(exc):
            raise

    for label, ok, extra in (
        ("mariadb", seen.database_ready, None),
        ("wordpress", seen.wordpress_ready, wp_problem),
        ("store-setup", seen.setup_done, None),
        ("woo-mcp", seen.mcp_ready, mcp_problem),
    ):
        if ok:
            continue
        detail = extra or problems.get(label)
        seen.waiting.append(f"{label} ({detail})" if detail else label)
    return seen


# ----------------------------------------------------------------------- Helm


@dataclass
class HelmResult:
    ok: bool
    output: str


async def _helm(*args: str, timeout: float = 180) -> HelmResult:
    env = {**os.environ}
    with anyio.move_on_after(timeout) as scope:
        proc = await anyio.run_process(["helm", *args], check=False, env=env)
        out = (proc.stdout + proc.stderr).decode(errors="replace").strip()
        return HelmResult(proc.returncode == 0, out[-2000:])
    assert scope.cancelled_caught
    return HelmResult(False, f"helm {args[0]} took longer than {timeout:g}s")


async def helm_status(namespace: str, release: str) -> dict[str, Any] | None:
    """The release's state as Helm records it, or None if there is no release."""
    result = await _helm("status", release, "-n", namespace, "-o", "json", timeout=60)
    if not result.ok:
        if "not found" in result.output.lower():
            return None
        raise RuntimeError(result.output)
    return json.loads(result.output)


def store_values(slug: str, display_name: str, settings: Settings) -> dict[str, Any]:
    host = f"{slug}.{settings.store_domain}"
    return {
        "credentials": {"existingSecret": SECRET_NAME},
        "wordpress": {"publicUrl": f"http://{host}"},
        "ingress": {"host": host},
        "storeSetup": {"title": display_name, "admin": {"email": settings.store_admin_email}},
        "mcp": {
            "image": {"repository": settings.mcp_image_repository, "tag": settings.mcp_image_tag},
            "gateway": {"namespace": settings.own_namespace, "podLabels": {"app.kubernetes.io/component": "gateway"}},
        },
    }


async def helm_install(slug: str, display_name: str, settings: Settings) -> HelmResult:
    """`helm upgrade --install`, without waiting: the reconcile loop watches readiness.

    The per-store values go in a file, not --set, so a store title with commas
    or quotes reaches the chart unchanged.
    """
    namespace = state.namespace_for(slug)
    chart = Path(settings.chart_dir)
    with tempfile.TemporaryDirectory() as tmp:
        values = Path(tmp) / "store.json"
        values.write_text(json.dumps(store_values(slug, display_name, settings)))
        return await _helm(
            "upgrade", "--install", slug, str(chart), "--namespace", namespace,
            "-f", str(chart / "values-tenant.yaml"), "-f", str(values),
            "--timeout", "3m", timeout=240,
        )


async def helm_uninstall(namespace: str, release: str) -> HelmResult:
    result = await _helm("uninstall", release, "-n", namespace, "--wait", "--timeout", "2m", timeout=150)
    if not result.ok and "not found" in result.output.lower():
        return HelmResult(True, result.output)
    return result
