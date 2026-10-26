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
| Dashboard and chat | https://app.34-67-189-86.sslip.io |
| MCP endpoint | `https://app.34-67-189-86.sslip.io/mcp`, header `Authorization: Bearer <API key>`, or a browser sign-in (OAuth) |
| The store | https://shop.34-67-189-86.sslip.io (checkout uses cash on delivery) |

**Accounts.**

- The admin is `admin@urumi.test`. Its password is the `admin-password` key in
  `.runtime/app/secrets.json` on the coding workspace.
- Admins invite people from the Team page.
- Four members (Ana, Bob, Chen, Dev) already exist on the VM. Their passwords
  and keys are in `~/.urumi/vm-team-credentials.json` on the coding workspace.

**Connect Claude Code or Cursor.** Make a key on the Connect page. It shows the
exact command or config. For Claude Code:

```bash
claude mcp add --transport http urumi https://app.34-67-189-86.sslip.io/mcp \
  --header "Authorization: Bearer urumi_..."
```

Or skip the key and sign in with the browser. Add the server without a header,
then run `/mcp` in Claude Code and pick Authenticate:

```bash
claude mcp add --transport http urumi https://app.34-67-189-86.sslip.io/mcp
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

## Read more

| Document | What it covers |
|---|---|
| [docs/design.md](docs/design.md) | How each mechanism works, how it fails, how it was checked, and the alternatives. It also covers Helm rollback and what it can't undo. |
| [docs/threat-model.md](docs/threat-model.md) | Attackers, trust boundaries, what stops each threat, what is still open |
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
| `services/chat-agent` | Dashboard chat: OpenAI Agents SDK, Kimi K3 on Bedrock |
| `dashboard` | React + TypeScript dashboard |
| `charts/urumi-store`, `charts/urumi-app` | The two Helm charts |
| `infra/gcp`, `infra/cluster`, `scripts/gcp-vm.sh` | The VM, the cluster add-ons and the commands that run them |
| `console`, `lessons`, `labs`, `video` | Learning material made while building this; not part of the system |

## Run it

**On a laptop, without Kubernetes:**

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
| gateway | 12 |
| chat-agent | 26, plus one live test against Bedrock (`RUN_LIVE_BEDROCK=1`) |
| slack-mcp | 14 |
| woo-mcp | 10 |
| demo-mcps | 3 |
