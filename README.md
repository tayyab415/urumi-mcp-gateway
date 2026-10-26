# Urumi team MCP gateway on Kubernetes

One endpoint gives a team's AI clients access to four MCP servers. The clients
are Claude Code, Cursor and the dashboard's own chat. The servers are a real
WooCommerce store, a shipping service, a supplier and a Slack channel.

The gateway:

- checks every request against the person's own key and role;
- keeps the upstream credentials, so no team member ever holds them;
- records every tool call under the person who made it.

Everything runs on Kubernetes and installs with two Helm charts.

## Try it

| What | Where |
|---|---|
| Dashboard and chat | https://urumi-1027824348124.us-central1.run.app |
| MCP endpoint | `https://urumi-1027824348124.us-central1.run.app/mcp`, header `Authorization: Bearer <API key>`, or a browser sign-in (OAuth) |
| The store | https://shop.34-67-189-86.sslip.io (checkout uses cash on delivery) |
| More stores | Stores page in the dashboard; each new store is `http://<name>.34-67-189-86.sslip.io` |
| Search mode (for clients that load every tool) | `https://urumi-1027824348124.us-central1.run.app/mcp/search`: two tools, search and call |

The `run.app` address is a Cloud Run service that only forwards requests
([infra/cloud-run](infra/cloud-run/Caddyfile)). Everything runs on the
Kubernetes cluster on the VM, which also answers directly at
https://app.34-67-189-86.sslip.io.

**Accounts.**

- The admin is `admin@urumi.test`. Its password is the `admin-password` key in
  `.runtime/app/secrets.json` on the coding workspace.
- Admins invite people from the Team page.
- Four members (Ana, Bob, Chen, Dev) already exist on the VM. Their passwords
  and keys are in `~/.urumi/vm-team-credentials.json` on the coding workspace.

**Connect Claude Code or Cursor.** Make a key on the Connect page. It shows the
exact command or config. For Claude Code:

```bash
claude mcp add --transport http urumi https://urumi-1027824348124.us-central1.run.app/mcp \
  --header "Authorization: Bearer urumi_..."
```

Or skip the key and sign in with the browser. Add the server without a header,
then run `/mcp` in Claude Code and pick Authenticate:

```bash
claude mcp add --transport http urumi https://urumi-1027824348124.us-central1.run.app/mcp
```

Use one or the other. Claude Code never tries OAuth when a static
`Authorization` header is set. You can disconnect a browser sign-in on the
Connect page.

## How it fits together

```text
 Claude Code, Cursor             Browser
     (API key)               (login cookie)
         │                         │
         └──── Traefik: HTTPS, one hostname, rate limits ────┐
         │                         │                         │
         ▼                         ▼                         ▼
       /mcp ◀──── chat service ◀── /api/chat           dashboard (/)
         │       (Kimi K3 on Bedrock,
         │        user's chat token)
         ▼
 ┌──────────────────────── gateway ────────────────────────┐
 │ credential → visible tools → role → call → audit row     │   users, keys, servers,
 └────┬─────────────┬──────────────┬──────────────┬─────────┘   audit: SQLite on a volume
      ▼             ▼              ▼              ▼
   woo-mcp       shipping       supplier      slack-mcp ──▶ one Slack channel
      │         (simulated)    (simulated)
      ▼
 WordPress + WooCommerce + MariaDB          (namespace urumi-store)
```

Chat and external clients use the same `/mcp` endpoint, so they get the same
tools, the same permission checks and the same audit log. Only the gateway can
open connections to the MCP servers; NetworkPolicies enforce that.

## The required journey

| Step | How it works | Checked |
|---|---|---|
| A shopper checks out, and the order shows in WooCommerce | The real store, with persistent volumes | Order 44 survived replacing both the WordPress and MariaDB Pods |
| User A creates a product through chat | Chat holds `woo__create_product` until A approves, then calls it through the gateway as A | Admin approval and denial, live on the VM |
| A retrieves today's orders through chat | `woo__list_orders` with `period: today` | Live through the public URL, 2026-10-26 |
| User B connects an external client with the URL and a key | Connect page; the key is checked on every request | An MCP client over the internet listed 13 tools |
| B sees tools from every enabled server | `tools/list` returns the enabled servers' tools that B's role allows | Members see 9 of the 13 |
| One prompt uses two upstreams | Tool names are `<server>__<tool>`, so one session holds all servers | Claude Code as Bob, over the internet, called `shipping__quote_shipping` and `supplier__list_products` in one prompt; the audit log credits Bob's key |
| Revoking B's key rejects the next call, on an open connection too | The credential is checked per HTTP request, not per session | Gateway test and a live check |
| Disabling a server removes its tools from chat and external clients | It's gone from the next `tools/list`, and direct calls are refused | Gateway and chat tests and a live check |
| Killing an upstream Pod leaves the others working | A fresh connection per call, a 10 s deadline, and readiness that ignores upstreams | Supplier killed mid-use: shipping still answered, and the dashboard showed it down, then ok |
| Audit rows name the right person and outcome | One row per attempt, refused calls included | Five people in parallel, each attributed |

## Bonus and stand-out items

The brief's optional bonus and "other ways to stand out", what was done, and
where to look.

| Item | What we did | Where |
|---|---|---|
| **Bonus: multi-store provisioning** | Create and delete stores from the dashboard. Each gets its own namespace, database, volumes, Secret, Ingress host and WooCommerce MCP server, which the gateway registers by itself (`store-<name>__*`). NetworkPolicy isolation, ResourceQuota, LimitRange, 2 stores per person, a 15-minute deadline, restart-safe. Checked on the VM, including the brief's test (chat compares two stores, then one is deleted) | [docs/multi-store.md](docs/multi-store.md), `services/provisioner`, `scripts/verify-multistore.sh` |
| 1. VPS deployment | The same charts on a GCP VM (k3s in k3d), public over HTTPS with cert-manager | [infra/gcp/README.md](infra/gcp/README.md), design note §11-12 |
| 2. Tool permissions | Per-role, per-tool switches; hidden from lists and refused if called; provisioned stores' tools are visible to their owner and admins only | Permissions page, design note §5 |
| 3. OAuth on the gateway | The gateway is its own OAuth authorization server per the MCP authorization spec: discovery, dynamic registration, PKCE, audience-bound opaque tokens, refresh rotation, consent page, "Connected apps" with disconnect | design note §13, `gateway/oauth.py` |
| 4. Handling many tools | Search mode at `/mcp/search`: `gateway__search_tools` (BM25) and `gateway__call_tool`, same permissions and audit as direct calls | design note §14, `gateway/toolsearch.py` |
| 6. Security hardening | Tool pinning against changed or injected tool descriptions; SSRF checks at save and connect time; default-deny NetworkPolicies; non-root Pods (Pod Security "restricted"); least-privilege RBAC plus an admission policy for the provisioner; upstream credentials encrypted at rest; Host check, POST-only `/mcp` | design note §15-16, [docs/threat-model.md](docs/threat-model.md) |
| 7. Scaling (partial) | Rate limits per API key and per chat session at the Ingress. One gateway replica only (SQLite) | design note §11 |
| 8. Observability (partial) | Calls, errors, p50/p95 per server and the slowest tools on the Overview page; clear reasons for unreachable upstreams and failed calls | Overview page, design note §7-8 |
| 9. Upgrades and rollback | Content-hashed image tags, `helm upgrade`, a rollback done live, and what rollback cannot undo | design note §12 |

## Read more

| Document | What it covers |
|---|---|
| [docs/design.md](docs/design.md) | How each mechanism works, how it fails, how it was checked, and the alternatives. It also covers Helm rollback and what it can't undo. |
| [docs/threat-model.md](docs/threat-model.md) | Attackers, trust boundaries, what stops each threat, what is still open |
| [docs/multi-store.md](docs/multi-store.md) | The multi-store bonus: provisioner, permissions, isolation, guardrails, recovery |
| [charts/urumi-app/README.md](charts/urumi-app/README.md) | The app chart, NetworkPolicies, the Secret, public access, TLS and rate limits |
| [charts/urumi-store/README.md](charts/urumi-store/README.md) | The store chart: WordPress, MariaDB, the setup Job and persistence |
| [services/README.md](services/README.md) | Each service, its contracts, running it all locally |
| [services/chat-agent/README.md](services/chat-agent/README.md) | The chat agent, the approval gate and the model choice |
| [infra/gcp/README.md](infra/gcp/README.md) | The VM: costs, firewall, static IP and everyday commands |
| [dashboard/README.md](dashboard/README.md) | The React dashboard |

## Repository

| Path | Contents |
|---|---|
| `services/gateway` | The gateway: MCP endpoint, dashboard API, users, keys, roles, audit |
| `services/woo-mcp` | The WooCommerce MCP server: five tools over the REST API |
| `services/demo-mcps` | Shipping and supplier MCP servers, simulated and labelled as such |
| `services/slack-mcp` | Slack MCP server for one channel |
| `services/provisioner` | Creates and deletes stores with Helm, one namespace each |
| `services/chat-agent` | Dashboard chat: OpenAI Agents SDK, Kimi K3 on Bedrock |
| `dashboard` | React + TypeScript dashboard |
| `charts/urumi-store`, `charts/urumi-app` | The two Helm charts |
| `infra/gcp`, `infra/cluster`, `scripts/gcp-vm.sh` | The VM, the cluster add-ons and the commands that run them |
| `console`, `lessons`, `labs`, `video` | Learning material made while building this; not part of the system |

## Run it

**On local Kubernetes (k3d)**, with the local values files only. Needs
Docker, k3d, Helm and kubectl:

```bash
python3 scripts/create-store-secret.py   # random passwords and a WooCommerce REST key
python3 scripts/create-app-secret.py     # gateway key, admin password, upstream tokens
bash scripts/local-up.sh                 # cluster, images, both charts (about 10 minutes)
```

**Local domains.** Add `127.0.0.1 shop.urumi.test app.urumi.test` to
`/etc/hosts`. k3d publishes Traefik on `127.0.0.1:18080`, so the store is
`http://shop.urumi.test:18080` and the dashboard and `/mcp` are on
`http://app.urumi.test:18080`. `.test` is reserved for testing and never
resolves on the internet. Browser sign-in (OAuth) needs https, so locally use
an API key from the Connect page. `bash scripts/local-up.sh down` removes it
all. Checked on 2026-10-26 on a fresh k3d cluster: both charts were Ready in
about 4.5 minutes, and an MCP call went gateway → woo-mcp → WordPress
([docs/evidence/14-local-k3d-install.txt](docs/evidence/14-local-k3d-install.txt)).

**On a laptop, without Kubernetes** (fastest for working on the code):

```bash
bash scripts/dev-stack.sh start   # fake store, MCP servers and gateway
bash scripts/dev-ui.sh start      # chat on :8200, dashboard on :5173
```

**On the GCP VM** (k3d, one node). See [infra/gcp/README.md](infra/gcp/README.md):

```bash
python3 scripts/create-store-secret.py && bash scripts/gcp-vm.sh deploy-store
python3 scripts/create-app-secret.py
bash scripts/gcp-vm.sh build-images && bash scripts/gcp-vm.sh deploy-app
```

The same two charts install anywhere. Machine-specific settings (hostnames, TLS,
storage) live in values files: `values-local.yaml`, the VM's files under
`.runtime/`, and `values-prod.yaml` as an example.

## Tests

Each service has its own: `cd services/<name> && uv run pytest -q`.

| Service | Tests |
|---|---|
| gateway | 45 (MCP, roles, OAuth, search mode, pinning, SSRF, stores) |
| chat-agent | 26, plus one live test against Bedrock (`RUN_LIVE_BEDROCK=1`) |
| slack-mcp | 14 |
| woo-mcp | 10 |
| demo-mcps | 3 |
| provisioner | 15 |
