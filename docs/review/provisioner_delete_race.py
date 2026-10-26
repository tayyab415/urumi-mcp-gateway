"""A delete during Helm install is overwritten by the in-flight reconciliation."""
import asyncio
import copy
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import patch

from provisioner import cluster, state
from provisioner.app import Provisioner
from provisioner.config import Settings

cfg = Settings(
    "x" * 20, "example.invalid", "review@example.invalid", "unused", "unused",
    "unused", "urumi-app", "urumi-provisioner", "urumi-store-tenant", 2, 4, 900, 3, 5,
)
annotations = {
    state.ANN_PHASE: "Provisioning",
    state.ANN_NAME: "Review deletion fixture",
    state.ANN_CREATED: state.iso(state.now_utc()),
    state.ANN_DEADLINE: state.iso(state.now_utc() + timedelta(seconds=900)),
    state.ANN_ATTEMPTS: "0",
}
namespace = NS(
    metadata=NS(
        name="store-review-fixture",
        labels={state.LABEL_STORE: "true", state.LABEL_SLUG: "review-fixture", state.LABEL_OWNER: "4"},
        annotations=annotations,
    ),
    status=NS(phase="Active"),
)
operations = []


def annotate(name, changes):
    annotations.update(changes)
    operations.append(("annotate", changes.copy()))


async def main():
    started, release = asyncio.Event(), asyncio.Event()

    async def status(*args):
        return None

    async def install(*args):
        started.set()
        await release.wait()
        return cluster.HelmResult(True, "", "")

    def observed(*args):
        return state.Observed(database_ready=True, wordpress_ready=True, mcp_ready=True, setup_done=True)

    provisioner = Provisioner(cfg)
    with patch.multiple(
        cluster, annotate=annotate, read_namespace=lambda *a: copy.deepcopy(namespace),
        ensure_rolebinding=lambda *a: None, ensure_secret=lambda *a: None,
        helm_status=status, helm_install=install, observe=observed,
        delete_namespace=lambda *a: operations.append(("namespace_delete", a)),
    ):
        inflight = asyncio.create_task(provisioner.reconcile(copy.deepcopy(namespace), "review-fixture"))
        await started.wait()
        result = await provisioner.delete("review-fixture")
        print("Delete API response:", result["phase"])
        print("Namespace phase immediately after delete:", annotations[state.ANN_PHASE])
        release.set()
        await inflight
        print("Namespace phase when prior reconciliation finishes:", annotations[state.ANN_PHASE])
        print("Namespace deletion called:", any(op[0] == "namespace_delete" for op in operations))
        print("Operations:", operations)


asyncio.run(main())
