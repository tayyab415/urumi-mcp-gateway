# Design note

How the system works and why it is built this way. Each section answers the
brief's five questions: what problem the piece solves, where it lives, what
happens when it fails, how it was checked, and what simpler option was passed
over. The threat model is separate: [threat-model.md](threat-model.md).

```text
 Claude Code / Cursor            Browser
   (API key)                 (login cookie)
       │                          │
       │      Traefik Ingress, HTTPS, rate limits
       │          │               │
       ▼          ▼               ▼
      /mcp ◀── chat service ◀── dashboard (/api, /api/chat)
       │      (chat token)
       ▼
   ┌───────────────────────────── gateway ─────────────────────────────┐
   │ check credential → visible tools → role check → call → audit row   │
   └───────┬──────────────┬───────────────┬────────────────┬───────────┘
           ▼              ▼               ▼                ▼
        woo-mcp        shipping        supplier         slack-mcp
           │          (simulated)     (simulated)          │
           ▼                                               ▼
   WordPress + WooCommerce + MariaDB                  one Slack channel
```

## 1. One gateway, two doors

**Problem.** A team wants one place to connect AI clients to several MCP
servers, without handing every person every upstream credential, and with one
record of who did what.

**How.** The gateway is an MCP *server* to its clients and an MCP *client* to
each upstream. External clients come in on `/mcp` with an API key. The
dashboard chat comes in on the same `/mcp`, with a chat token tied to the
user's login session. Both end in `Gateway.call_tool` in
`services/gateway/gateway/core.py`, so the tool list, the role check, the
timeouts and the audit row are the same code for both. The chat service has no
tool code of its own; it is just another MCP client.

**When it fails.** If the gateway is down, every client loses every tool. That
is the cost of one choke point. Readiness (`/readyz`) checks only the gateway's
own database, so one dead upstream never takes the gateway out of service.

**Checked.** Chat calls and external calls land in the same audit log, with
`via = chat` or `via = mcp`.

**Simpler option.** Let chat call `woo-mcp` directly. It would be quicker to
build, but chat would then skip permissions and audit, which are the point.

## 2. Tool names and collisions

**Problem.** Both `woo-mcp` and the supplier have a `list_products` tool.

**How.** Public names are `<server slug>__<tool>`: `woo__list_products` and
`supplier__list_products`. Slugs cannot contain `__`, so the first `__` always
splits a name correctly. Names longer than 128 characters are skipped with a
warning. Each description starts with the server's name in brackets, so the
model can tell the two apart. `POST /api/servers/test` names any collisions
before an admin saves a new server.

**Checked.** A gateway test registers two upstreams that both offer
`list_products` and calls each one by its public name.

**Simpler option.** Reject a server whose tool names collide. That's safe, but
it fails on the supplier here, and on any real pair of e-commerce servers.

## 3. Checking the credential on every request

**Problem.** A revoked key must stop working at once, including on a
connection that is already open.

**How.** `ApiKeyAuth` in `mcp_endpoint.py` runs on every HTTP request to
`/mcp`, before the MCP layer: it looks the key up by its SHA-256 hash and
checks that the key is not revoked and the user is active. A chat token is
accepted only while its login session exists. A refused request gets HTTP 401
with a JSON-RPC error code of `-32030`. It is not `-32001`, because the MCP SDK
uses `-32001` for "request timed out", and chat used to report a revoked key as
"may have run".

**When it fails.** Every request costs one indexed database lookup. That is fine
at team size. A cache would make revocation slow to take effect.

**Checked.** A gateway test revokes a key and calls again on the same open
client. A chat test revokes the credential mid-turn and expects "did not run".
On the VM, the other session revoked Dev's key and both his chat and his
external client stopped.

**Simpler option.** Check the key once, at MCP `initialize`. Then a key revoked
mid-session keeps working until the client reconnects.

## 4. Three kinds of secret, three treatments

| Secret | Who needs the original | Stored as | Where |
|---|---|---|---|
| A person's password | nobody | scrypt hash, salted | gateway database |
| A person's API key, a login session | nobody (we only compare) | SHA-256 hash; the key is shown once | gateway database |
| An upstream's credential (shipping token, supplier key, Slack server token) | the gateway, to send it | encrypted (Fernet) with `gateway-secret-key` | encrypted in the database, key in a Kubernetes Secret |
| WooCommerce REST key | `woo-mcp`, to sign requests | plain, in the Secret | Kubernetes Secret, mounted only into `woo-mcp` |
| Bedrock API key | the chat service | plain, in the Secret | Kubernetes Secret, mounted only into chat |

Passwords get a slow hash because people choose guessable ones. API keys are
256 random bits, so a fast hash is enough and allows one indexed lookup.
Upstream credentials can't be hashed, because the gateway has to send them.
They are encrypted, and the key lives outside the database: a stolen database
file alone reveals no upstream credential.

The Bedrock key belongs to its own IAM user, `urumi-chat-bedrock`. That user may
call one model (Kimi K3) and nothing else in the AWS account, and the key
expires after 30 days.

**Simpler option.** Put every credential in environment variables. A dashboard
could then never add a server with its own credential, and every upstream
credential would sit in the gateway's environment as plain text.

## 5. Roles and per-tool permissions

**Problem.** Members should read orders, but only admins should create products
or change order status. One shared upstream credential means the upstream can't
tell people apart, so the gateway has to.

**How.** Each tool row stores `allowed_roles`. On first discovery, a tool that
declares `readOnlyHint: true` is open to members and everything else is
admin-only (`default_roles` in `core.py`). Admins change this on the
Permissions page. The gateway enforces the stored rule twice: a hidden tool is
missing from `tools/list`, and a direct call to it is refused and audited as
`denied`.

**When it fails.** The first default trusts the upstream's own hint. A server
that wrongly calls a write tool read-only opens it to members, until an admin
fixes it. The Permissions page shows every tool and its roles.

**Checked.** Gateway tests, and five simulated people on the VM: members saw 9
of 13 tools, and member attempts at writes were refused.

## 6. Disabling a server, and clients that remember old tools

**How.** Disabling a server drops its tools from the next `tools/list`. Clients
may still remember them, so a call to a disabled server's tool is refused and
audited. The catalog is refreshed every 30 seconds; each upstream refreshes on
its own, so a slow one delays only itself. A chat turn lists tools once at its
start, so a server disabled mid-conversation is gone on the next message.

If the model asks for a tool it does not have (disabled, hidden by role, or
made up), chat sends back "the call did not run" as the tool's result. The model
can then explain. The SDK's default ends the turn with an error instead. Such a
call never reaches the gateway, so it has no audit row; the chat service logs it.

**Checked.** Gateway and chat tests. On the VM: disabling Slack removed its
tools from Bob at once, and calling an old Slack tool name was rejected.

## 7. Timeouts, isolation and unknown outcomes

**Problem.** One hung upstream must not hang everything. A timed-out write is
neither a success nor a failure.

**How.** Each upstream call opens a fresh connection under a hard deadline
(`anyio.fail_after`, `gateway.upstreamTimeoutSeconds`, 10 seconds). Failures are
sorted into kinds: *unreachable* (the request never arrived), *timeout* (it
arrived, and there was no answer in time), *auth_failed* (the upstream rejected
the stored credential) and *protocol*. Each becomes a tool result with
`isError: true` and a plain sentence, so a client or model can read it. A timeout
on a tool that can change data says the outcome is unknown. Nothing is retried:
a retried `create_product` could make two products. Real calls also update the
server's status on the dashboard, not only the 30-second refresh.

**Checked.** Gateway tests use a slow upstream next to a healthy one. Chat tests
check the "outcome is unknown" wording on a timeout. On the VM the other session
killed the supplier Pod mid-use: chat reported it unreachable and still returned
the shipping quote, and the dashboard showed it down, then ok after the restart.

**Simpler option.** Keep one long-lived connection per upstream. That saves a
round trip, but a replaced Pod leaves the gateway holding a dead connection.

## 8. The audit log

Each tool call attempt writes one row: who, which key (by prefix) or which chat
conversation, server, tool, arguments (truncated JSON), outcome, duration and
error. Refused calls are recorded too. Store data passes through the gateway but
is never stored in it. The Audit page polls for new rows, and "Open run" shows
one chat conversation's calls.

## 9. The chat agent

- **Loop.** The OpenAI Agents SDK runs it: model → tool calls through the gateway
  → results → model. Every call goes out with the user's chat token, so the
  gateway sees the real person.
- **Approval gate.** A tool without `readOnlyHint` waits for the user to press
  Approve. A denied call never reaches the gateway.
- **Model.** Kimi K3 on Amazon Bedrock, through Bedrock's OpenAI-compatible
  Responses API. The user wanted to move off OpenAI's own models. Kimi K3
  worked with the existing code by changing only the model name, and made tool
  calls reliably. Amazon's own Nova models needed a different API (Converse),
  or have reached end of life.
- **No tracing.** Tracing is off, so prompts and tool results don't leave for a
  third-party trace service.
- **Memory.** Conversations live in the chat Pod's memory. A restart loses them.
  The audit log keeps the record of what was done.

## 10. Kubernetes

| Need | What the charts do |
|---|---|
| Keep data across Pod replacement | PVCs: MariaDB 8 GiB, WordPress uploads 8 GiB, gateway SQLite 1 GiB. All three survive `helm uninstall`. |
| One writer for SQLite | The gateway runs 1 replica with the `Recreate` strategy, so the old Pod stops before the new one opens the file. |
| Know when a Pod is ready | Startup, readiness and liveness probes on every workload. The gateway's readiness ignores upstreams. |
| Fair use of a small VM | Requests and limits on every container. |
| Keep secrets out of images and Git | Two Secrets, applied over SSH from files kept outside Git. The files are never copied to the VM; Kubernetes stores the Secrets in its own datastore. |
| Only the gateway reaches the MCP servers | No Ingress route to them, and NetworkPolicies: the namespace denies everything, then opens one path at a time. |
| Contain a compromised container | The "restricted" Pod Security Standard: non-root, no privilege escalation, no Linux capabilities, read-only root filesystem. |

Not having an Ingress route is different from having a NetworkPolicy. Without a
route, the internet can't reach `woo-mcp`, but any Pod in the cluster still
could. The NetworkPolicy closes that path. Checked: a Pod in another namespace,
and the chat Pod, could not connect to `woo-mcp`, while the gateway could.

**Simpler option for storage.** MariaDB for the gateway too. `aiomysql` is
installed and `DATABASE_URL` picks the engine, but only SQLite has been tested.
SQLite on a volume is one less moving part. The price is one gateway replica.

## 11. Getting in from the internet

The VM has a static IP and a firewall rule for ports 80 and 443 only. sslip.io
turns `app.34-67-189-86.sslip.io` into that IP, so no DNS setup is needed.
cert-manager gets Let's Encrypt certificates and renews them. Traefik redirects
HTTP to HTTPS and enforces rate limits before a request reaches a Pod: `/mcp`
and chat get an allowance per `Authorization` header (each key or chat session),
and login one per client address. k3d's load balancer hides client addresses, so
on this VM the login limit is one shared limit. Details:
[the app chart README](../charts/urumi-app/README.md#on-the-internet).

## 12. Helm: one chart, many places

Two charts: `charts/urumi-store` (WordPress, MariaDB, a setup Job that installs
WooCommerce, the catalog and the REST key) and `charts/urumi-app` (everything
else). Everything that differs between places is a value:

| | Local k3d | GCP VM | Production example |
|---|---|---|---|
| Hostnames | `*.urumi.test:18080` | `*.34-67-189-86.sslip.io` | your domain |
| TLS | off | cert-manager, Let's Encrypt | cert-manager or your own Secret |
| Login cookie | not `Secure` | `Secure` | `Secure` |
| Storage | `local-path` | `local-path` | the cluster's StorageClass |

There is no image registry. Images are built on the VM and imported into the
cluster. Each tag is a hash of the image's source, so an upgrade replaces only
the Pods whose code changed.

### What rollback can and cannot undo

Helm keeps every release's rendered manifests. `helm rollback` puts back the old
Deployments, image tags, ConfigMaps, Services, Ingresses and NetworkPolicies.
It does not touch:

- **Data on volumes.** The gateway's users, keys and audit rows, and the store's
  products and orders, stay as they are.
- **Database schema.** The gateway adds new columns when it starts. Older code
  ignores a column it doesn't know, so these additive changes roll back
  safely. A destructive change (a dropped or renamed column) would need a
  backup; there is none yet, beyond the VM's disk.
- **The Secrets.** They are applied outside Helm, so a rolled-back release uses
  today's Secret values.
- **Things outside the cluster.** A product created in WooCommerce, a Slack post,
  an IAM change.

Checked on the VM on 2026-10-26. Rolling back from revision 6 to 5 took 31
seconds. Revision 5's gateway image was back, and `/mcp`'s 401 code was `-32001`
again. The data didn't change: 5 users, 6 keys, 4 servers, 37 audit rows. That
includes a row written by revision 6's code a minute before. Rolling forward
again took 34 seconds, and the data stayed the same.

## What I would change for a bigger team

- **Database.** Move the gateway to Postgres or MariaDB and run several
  replicas. SQLite's single writer is the first limit.
- **Conversations.** Keep chat conversations in a database, so a restart or a
  second replica doesn't lose them.
- **Load balancer.** Use one that keeps client addresses, so the login limit is
  per address.
- **Secrets and images.** Use an image registry, and a secret manager (External
  Secrets or Sealed Secrets) instead of applying Secret files by hand.
- **Backups.** Take scheduled backups of the three volumes, and test a restore.
- **Upstream changes.** Show admins when an upstream changes a tool's
  description or hints.
