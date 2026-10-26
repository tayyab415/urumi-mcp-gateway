# Multi-store provisioning (the bonus)

A signed-in person creates a store from the dashboard's Stores page. A few
minutes later it has its own WordPress, WooCommerce, database, volumes, Secret
and web address, plus its own WooCommerce MCP server. The gateway registers
that server by itself, so the store's tools (`store-<name>__list_orders` and
the rest) show up in chat and in Claude Code or Cursor. Deleting the store
removes all of it.

```text
 Stores page ──POST /api/stores──▶ gateway ──PUT /stores/north──▶ provisioner
   (who may?        (owner check,            (limits, then writes the
    the gateway)     audit of tools)          record on a new namespace)
                                                     │ reconcile loop, every 5 s
                                                     ▼
                         namespace store-north ── helm upgrade --install north charts/urumi-store
                         ├─ MariaDB (StatefulSet, volume)       ├─ ResourceQuota, LimitRange
                         ├─ WordPress + WooCommerce (volume)    ├─ NetworkPolicies (deny by default)
                         ├─ setup Job (installs WooCommerce)    ├─ Secret store-credentials
                         ├─ woo-mcp (this store's MCP server)   └─ Ingress north.<domain>
                                                     │ Ready
 gateway sync loop, every 5 s ◀── GET /stores ───────┘
   registers server "store-north" → http://north-woo-mcp.store-north.svc:8000/mcp
```

## Where it lives

| Piece | Path |
|---|---|
| Provisioner service (API + reconcile loop) | `services/provisioner/provisioner/` (`app.py`, `cluster.py`, `state.py`) |
| Its Deployment, ServiceAccount, RBAC, admission policy | `charts/urumi-app/templates/provisioner.yaml` |
| Its NetworkPolicy, and the gateway's egress to stores | `charts/urumi-app/templates/networkpolicy.yaml` |
| What a store is made of | `charts/urumi-store` with `values-tenant.yaml`: `templates/woo-mcp.yaml`, `networkpolicy.yaml`, `guardrails.yaml` are new and off for the first store |
| Gateway side: Stores API, registration sync, owner-only tools | `services/gateway/gateway/stores.py`, `core.may_use_server` |
| Dashboard | `dashboard/src/pages/Stores.tsx` |
| Checks you can rerun | `scripts/verify-multistore.sh`, `scripts/store_cli.py` |

## How each requirement is met

**Create and delete from the dashboard, with status, URL and created time.**
The Stores page polls `GET /api/stores` every 3 seconds. Each store shows
Provisioning, Ready, Failed or Deleting, the reason while it is not ready
("Waiting for: wordpress; store-setup"), its address and when it was made.
Several stores provision at once: the provisioner works on each store in its
own task, and Helm runs per namespace.

**Kubernetes-native, one namespace per store.** The provisioner creates
namespace `store-<name>`, a Secret with fresh random passwords and a fresh
WooCommerce REST key, and then runs `helm upgrade --install` of the same store
chart the first store uses, with `values-tenant.yaml` on top. The chart makes
the database, volumes, WordPress, the setup Job, the MCP server, the Ingress
host `<name>.<domain>`, the quota and the network rules.

**Automatic MCP registration, with clear tool names.** The gateway asks the
provisioner for the stores every 5 seconds. A Ready store whose server is not
registered gets one, slug `store-<name>`, so its tools are
`store-north__list_orders`, `store-north__create_product`, and so on. The
gateway lists its tools at once, so they appear within seconds of Ready.

**Clean teardown.** Delete marks the store Deleting. The gateway removes the
registration in the same request, so its tools disappear from every client
before anything else happens. The provisioner then runs `helm uninstall` and
deletes the namespace, which takes the volumes, the Secret and Helm's own
record with it. Deleting is one way, even for a store that is still being
provisioned: the request is its own annotation (`urumi.io/delete-requested`),
which only Delete sets and nothing clears, and it outranks the phase. A
reconcile pass that was busy in Helm when the delete came re-reads the
namespace before it writes, and does not record "Ready"; the next pass deletes
the store.

**Isolation, shown with NetworkPolicies.** Each store namespace starts with
deny-all, in and out. Then: Traefik and this store's MCP server may reach
WordPress; only this store's WordPress and setup Job may reach its database;
only the gateway Pod (namespace `urumi-app`) may reach the MCP server, and the
MCP server may reach only this store's WordPress. Nothing names another store,
so there is no path between stores. `scripts/verify-multistore.sh` checks it
from inside the MCP server's Pod.

**Guardrails.**
- ResourceQuota per namespace (CPU, memory, Pods, volumes, storage) and a
  LimitRange with defaults and a per-container maximum.
- At most 2 stores per person and 4 in total (Helm values
  `provisioner.maxStoresPerUser`, `maxStoresTotal`). The provisioner counts
  namespaces, so the limit holds even if the gateway were bypassed.
- A provisioning deadline (15 minutes). A store not ready by then becomes
  Failed with what it was still waiting for, for example
  "wordpress (not scheduled (Insufficient memory))".

**Recovery.** The provisioner keeps no state of its own. A store's record is
labels and annotations on its namespace (owner, phase, reason, deadline), and
everything else is read from the cluster on each pass. So:
- Creating is safe to retry: `PUT /stores/north` for a store that exists
  returns it; for a Failed one it starts again with the same Secret and a new
  deadline. Starting again means running setup again: a setup Job that ran
  out of attempts (say, the WooCommerce download failed) never runs again by
  itself, and Helm sees nothing to change in a deployed release. So the
  provisioner deletes the failed Job and runs `helm upgrade --install` once
  more, which makes a fresh Job; the database and WordPress volumes stay.
- A provisioner restarted mid-way lists the store namespaces and carries on.
  If Helm was killed during an install, the release is left "pending"; only
  this one process runs Helm, so a pending release means a crash, and it is
  uninstalled and installed again (at most 3 attempts, then Failed with Helm's
  error).

## Who may do what

| Who | Create | See a store's tools | Delete |
|---|---|---|---|
| Any signed-in person | up to 2 stores | their own stores | their own stores |
| Admin | up to 2 stores | every store | any store |

A store's tools are hidden from other members in `tools/list` and refused if
called directly, the same way role limits work. Within your own store, the
usual per-tool roles still apply: a member owner can list orders but not
create products, unless an admin changes that on the Permissions page.

## The provisioner's permissions

It is the one component that talks to the Kubernetes API, so its access comes
in three layers (`charts/urumi-app/templates/provisioner.yaml`):

1. **Cluster-wide:** create, read and delete namespaces; create RoleBindings;
   and `bind` exactly one ClusterRole, `urumi-store-tenant`. It cannot read a
   Secret or touch a Deployment anywhere with this alone.
2. **Per store namespace:** a RoleBinding to `urumi-store-tenant` gives it what
   Helm needs (Deployments, StatefulSets, Jobs, Services, Ingresses, Secrets,
   ConfigMaps, volumes, NetworkPolicies, quotas) in that namespace only.
3. **An admission policy** (`ValidatingAdmissionPolicy`, checked by the API
   server on every write): the provisioner may create, change or delete things
   only in namespaces labelled `urumi.io/store=true`, and may not add that
   label to a namespace that lacks it. Without this layer, "create
   RoleBindings" would let it grant itself the tenant role in `urumi-app` and
   read the gateway's Secret. `verify-multistore.sh` tries exactly that and
   shows it refused.

   The same policy keeps each store's Pod Security fence, which namespace
   rights would otherwise let the provisioner lift. A namespace it creates must
   be named `store-*` and enforce `baseline` or `restricted` (it sets
   `baseline`, because WordPress's Apache starts as root). An update may not
   lower that level, remove it, or pin an older Pod Security version. And the
   Pods and Pod templates it writes (Deployments, StatefulSets, Jobs) may not
   share the host's process, IPC or network namespace, run privileged
   containers or mount `hostPath` volumes. These are checked on the
   workload itself, before any Pod exists.

## Choices and what they cost

- **Helm inside the provisioner, not a Kubernetes operator.** The same chart
  installs the first store by hand and every later store, so there is one
  definition of a store. An operator with a custom resource would be the
  production shape; it is more code than this needs.
- **The cluster is the database.** No provisioning table to keep in step with
  reality, and restarts are free. The cost: listing stores reads namespaces,
  which is fine for tens of stores.
- **One provisioner replica.** That is what makes "a pending Helm release
  means a crash" true. Two replicas would need a lease (leader election).
- **Plain HTTP for provisioned stores.** Let's Encrypt limits certificates per
  domain, and sslip.io is shared with everyone. Production would use a
  wildcard certificate (DNS-01) for `*.stores.example.com`. The first store
  and the dashboard keep HTTPS.
- **No credential between gateway and a store's MCP server.** Same as the
  first store: the NetworkPolicy is what keeps everyone else out, and the
  checks above show it. A per-store bearer token would be a second layer.
- **A smaller catalog for new stores** (four products, no photos), so a store
  is ready in about two minutes.

## How it was checked (VM, 2026-10-26)

- Ana and Bob created `north` and `south` at the same moment, then Ana created
  `east`: three stores provisioning at once, all Ready in about 2 minutes
  20 seconds. Asking again for `north` returned it (HTTP 200, no second
  store). Ana's third store was refused: "You already have 2 stores, the
  most one person may have."
- A shopper checked out on North and on East in the browser
  (`docs/evidence/01-multistore-checkouts.mp4`). Both orders are #15, since
  each store has its own database.
- `scripts/verify-multistore.sh north south`: 17 of 17 checks passed
  (`docs/evidence/02-verify-multistore.txt`).
- The admission policy's Pod Security and workload rules were checked off the
  VM: server-side dry runs as the provisioner's ServiceAccount against a local
  API server of the VM's Kubernetes version (1.36.5). Relabelling a store
  `privileged`, removing its level, creating a store namespace without one,
  and Deployments, StatefulSets and Jobs with host access were all refused;
  the provisioner's own calls and a Helm install of the store chart passed.
  The same Helm version (4.3.1) also confirmed that a deleted setup Job comes
  back, fresh, on the next `helm upgrade --install`.
- Unit and API tests: `services/provisioner` (18, including a retry after a
  failed setup Job and a delete that arrives mid-install), `services/gateway`
  `tests/test_stores.py` (create, retry, limit, registration, owner-only
  tools, delete removes tools).
