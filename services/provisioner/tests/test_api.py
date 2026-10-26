"""The internal API with the cluster faked: auth, limits, retry-safe create."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from provisioner import app as app_module, cluster
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


def test_delete_marks_deleting(client):
    c, spaces = client
    c.put("/stores/north", json={"name": "North", "owner_id": 7, "owner_email": "a@b"}, headers=TOKEN)
    assert c.delete("/stores/north", headers=TOKEN).json()["phase"] == "Deleting"
    assert c.delete("/stores/nope", headers=TOKEN).status_code == 404
