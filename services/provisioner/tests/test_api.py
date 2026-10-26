"""The internal API with the cluster faked: auth, limits, retry-safe create."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from provisioner import app as app_module, cluster
from provisioner.state import Observed
from tests.test_values import settings

TOKEN = {"Authorization": "Bearer " + "t" * 20}


def fake_ns(name, labels, annotations):
    return SimpleNamespace(metadata=SimpleNamespace(name=name, labels=labels, annotations=annotations), status=SimpleNamespace(phase="Active"))


@pytest.fixture
def client(monkeypatch):
    spaces: dict[str, SimpleNamespace] = {}
    monkeypatch.setattr(cluster, "load_config", lambda: None)
    monkeypatch.setattr(cluster, "list_store_namespaces", lambda: list(spaces.values()))
    monkeypatch.setattr(cluster, "read_namespace", lambda name: spaces.get(name))

    def create(slug, labels, annotations):
        spaces[f"store-{slug}"] = fake_ns(f"store-{slug}", labels, annotations)
        return spaces[f"store-{slug}"]

    monkeypatch.setattr(cluster, "create_namespace", create)
    monkeypatch.setattr(cluster, "annotate", lambda name, values: spaces[name].metadata.annotations.update(values))

    async def no_loop(self):
        return None

    monkeypatch.setattr(app_module.Provisioner, "run", no_loop)
    with TestClient(app_module.create_app(settings())) as c:
        yield c, spaces


def test_token_is_required(client):
    c, _ = client
    assert c.get("/stores").status_code == 401
    assert c.get("/stores", headers=TOKEN).json() == []


def test_create_is_safe_to_repeat_and_limited_per_owner(client):
    c, spaces = client
    body = {"name": "North", "owner_id": 7, "owner_email": "ana@urumi.test"}
    r = c.put("/stores/north", json=body, headers=TOKEN)
    assert r.status_code == 201 and r.json()["phase"] == "Provisioning"
    assert c.put("/stores/north", json=body, headers=TOKEN).status_code == 200  # a retry, not a second store
    assert len(spaces) == 1
    assert c.put("/stores/north", json={**body, "owner_id": 8}, headers=TOKEN).status_code == 409
    assert c.put("/stores/east", json=body, headers=TOKEN).status_code == 201
    r = c.put("/stores/west", json=body, headers=TOKEN)
    assert r.status_code == 403 and "most one person" in r.json()["detail"]
    assert c.put("/stores/Bad_Name", json=body, headers=TOKEN).status_code == 422


def test_retrying_a_failed_store_restarts_it(client):
    c, spaces = client
    body = {"name": "North", "owner_id": 7, "owner_email": "ana@urumi.test"}
    c.put("/stores/north", json=body, headers=TOKEN)
    spaces["store-north"].metadata.annotations["urumi.io/phase"] = "Failed"
    r = c.put("/stores/north", json=body, headers=TOKEN)
    assert r.status_code == 200 and r.json()["phase"] == "Provisioning"


def fake_helm_world(monkeypatch, job: dict, calls: list) -> None:
    """A store with a deployed Helm release, healthy Pods and a setup Job that is failed or not."""
    monkeypatch.setattr(cluster, "ensure_rolebinding", lambda *a: None)
    monkeypatch.setattr(cluster, "ensure_secret", lambda *a: None)

    async def status(*a):
        return {"info": {"status": "deployed"}}

    async def install(*a):
        calls.append("helm install")
        job["failed"] = False  # Helm made a fresh Job, which has not failed
        return cluster.HelmResult(True, "")

    def delete_failed_job(namespace, release):
        calls.append("delete failed job")
        return job["failed"]

    def observe(*a):
        return Observed(database_ready=True, wordpress_ready=True, mcp_ready=True, waiting=["store-setup"],
                        setup_failed="BackoffLimitExceeded: plugin download outage" if job["failed"] else None)

    monkeypatch.setattr(cluster, "helm_status", status)
    monkeypatch.setattr(cluster, "helm_install", install)
    monkeypatch.setattr(cluster, "delete_failed_setup_job", delete_failed_job)
    monkeypatch.setattr(cluster, "observe", observe)


def test_retrying_after_a_failed_setup_job_runs_setup_again(client, monkeypatch):
    c, spaces = client
    body = {"name": "North", "owner_id": 7, "owner_email": "ana@urumi.test"}
    c.put("/stores/north", json=body, headers=TOKEN)
    ns, prov = spaces["store-north"], c.app.state.provisioner
    job, calls = {"failed": True}, []
    fake_helm_world(monkeypatch, job, calls)

    # The setup Job ran out of attempts, so the store failed.
    asyncio.run(prov.reconcile(ns, "north"))
    assert ns.metadata.annotations["urumi.io/phase"] == "Failed" and "BackoffLimitExceeded" in ns.metadata.annotations["urumi.io/reason"]
    assert calls == []  # the release was deployed, so nothing was installed

    # Retry: the failed Job goes and Helm installs again, so setup really runs again.
    assert c.put("/stores/north", json=body, headers=TOKEN).json()["phase"] == "Provisioning"
    asyncio.run(prov.reconcile(ns, "north"))
    assert calls == ["delete failed job", "helm install"]
    assert ns.metadata.annotations["urumi.io/phase"] == "Provisioning"
    assert ns.metadata.annotations["urumi.io/reason"] == "Waiting for: store-setup"
    assert not ns.metadata.annotations.get("urumi.io/retry-requested")

    # Later passes only watch the new Job.
    asyncio.run(prov.reconcile(ns, "north"))
    assert calls == ["delete failed job", "helm install"]


def test_a_delete_during_a_reconcile_pass_is_not_undone(client, monkeypatch):
    c, spaces = client
    c.put("/stores/north", json={"name": "North", "owner_id": 7, "owner_email": "a@b"}, headers=TOKEN)
    ns, prov = spaces["store-north"], c.app.state.provisioner
    done: list[str] = []
    monkeypatch.setattr(cluster, "ensure_rolebinding", lambda *a: None)
    monkeypatch.setattr(cluster, "ensure_secret", lambda *a: None)
    monkeypatch.setattr(cluster, "observe", lambda *a: Observed(setup_done=True, database_ready=True, wordpress_ready=True, mcp_ready=True))
    monkeypatch.setattr(cluster, "delete_namespace", lambda name: done.append("delete namespace"))

    async def uninstall(*a):
        done.append("helm uninstall")
        return cluster.HelmResult(True, "")

    async def no_release(*a):
        return None

    monkeypatch.setattr(cluster, "helm_uninstall", uninstall)
    monkeypatch.setattr(cluster, "helm_status", no_release)

    async def race() -> dict:
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_install(*a):
            started.set()
            await release.wait()
            return cluster.HelmResult(True, "")

        monkeypatch.setattr(cluster, "helm_install", slow_install)
        # The pass works from the namespace as it read it, before the delete.
        inflight = asyncio.create_task(prov.reconcile(copy.deepcopy(ns), "north"))
        await started.wait()
        out = await prov.delete("north")
        release.set()
        await inflight
        return out

    assert asyncio.run(race())["phase"] == "Deleting"
    # The pass found everything ready, but the store stays Deleting...
    assert ns.metadata.annotations["urumi.io/phase"] == "Deleting"
    assert c.get("/stores", headers=TOKEN).json()[0]["phase"] == "Deleting"
    # ...and the next pass deletes it.
    asyncio.run(prov.reconcile(ns, "north"))
    assert done == ["helm uninstall", "delete namespace"]


def test_delete_marks_deleting(client):
    c, spaces = client
    c.put("/stores/north", json={"name": "North", "owner_id": 7, "owner_email": "a@b"}, headers=TOKEN)
    assert c.delete("/stores/north", headers=TOKEN).json()["phase"] == "Deleting"
    assert c.delete("/stores/nope", headers=TOKEN).status_code == 404


def test_helm_status_parses_long_output(monkeypatch):
    """A release's JSON is many kilobytes; status must parse all of it (a restart bug, fixed)."""
    import asyncio
    import json as _json

    big = _json.dumps({"info": {"status": "deployed"}, "manifest": "x" * 10_000})

    async def fake_helm(*args, timeout=180):
        return cluster.HelmResult(True, big, "")

    monkeypatch.setattr(cluster, "_helm", fake_helm)
    assert asyncio.run(cluster.helm_status("store-north", "north"))["info"]["status"] == "deployed"


def test_deadline_fails_a_store_whose_steps_keep_erroring(client, monkeypatch):
    import asyncio

    c, spaces = client
    c.put("/stores/north", json={"name": "North", "owner_id": 7, "owner_email": "a@b"}, headers=TOKEN)
    ns = spaces["store-north"]
    ns.metadata.annotations["urumi.io/deadline"] = "2000-01-01T00:00:00Z"
    prov = c.app.state.provisioner

    async def boom(*a, **k):
        raise RuntimeError("helm is broken")

    monkeypatch.setattr(prov, "reconcile", boom)
    asyncio.run(prov._reconcile_guarded(ns, "north"))
    assert ns.metadata.annotations["urumi.io/phase"] == "Failed"
    assert "helm is broken" in ns.metadata.annotations["urumi.io/reason"]
