# Services

| Service | What it is | Port (local) | Credential it holds |
|---|---|---|---|
| `woo-mcp` | Our WooCommerce MCP server. Five tools over Streamable HTTP, calling the WooCommerce REST API v3. | 18912 | WooCommerce consumer key and secret |
| `demo-mcps` (shipping) | Simulated carrier quotes. Callers must send `Authorization: Bearer <token>`. | 18921 | none (it checks a token) |
| `demo-mcps` (supplier) | Simulated wholesale catalog and restocking. Callers must send `X-Supplier-Key`. Has a `list_products` tool on purpose, to show name collisions. | 18922 | none (it checks a key) |
| `slack-mcp` | Reads and posts in one Slack channel with one shared bot token. Three tools; callers send `Authorization: Bearer <token>`. | 18931 | Slack bot token |
| `gateway` | The team gateway: one MCP endpoint, the dashboard API, users, keys, audit. | 8080 | encrypted upstream credentials, hashed user keys |
| `chat-agent` | Dashboard chat (Kimi K3 on Bedrock through the OpenAI Agents SDK). Reaches tools only through the gateway. Holds tools that change data until the user approves them. | 8200 | Bedrock credential |
| `dashboard` (`../dashboard`) | The React dashboard. Static files; talks to the gateway and the chat service over `/api`. | 5173 (Vite) | none |

All four MCP pieces pin `mcp==2.3.0`. MCP servers run stateless with JSON responses, so a replaced Pod loses nothing.

## Run everything locally

```bash
bash scripts/dev-stack.sh start    # fake store, woo-mcp, shipping, supplier, gateway; registers the three servers
bash scripts/dev-stack.sh status
bash scripts/dev-stack.sh stop
```

The fake store stands in for WooCommerce until the real one runs. It checks
WooCommerce's OAuth 1.0a signatures the way WooCommerce does.

```bash
bash scripts/dev-ui.sh start       # chat service on :8200 and the dashboard on :5173
```

Dashboard login: `admin@urumi.test` / `admin-password-1`. API docs: <http://localhost:8080/api/docs>.

## Seed the team

The gateway starts with one admin. `gateway.seed` adds the rest of the team and registers upstream servers, through the gateway's own HTTP API. It works on a laptop and as a Kubernetes Job, and running it twice changes nothing.

```bash
cd services/gateway
BOOTSTRAP_ADMIN_EMAIL=admin@urumi.test BOOTSTRAP_ADMIN_PASSWORD=admin-password-1 \
  uv run python -m gateway.seed --url http://localhost:8080 --config seed/team.local.json \
  --credentials-out ~/.urumi/team-credentials.json --keys
```

`seed/team.local.json` lists four members (Ana, Bob, Chen and Dev) and the upstream servers. The admin is the bootstrap account the gateway creates on first start. New people get random passwords and one API key each. Those go to the credentials file (mode 600) and nowhere else. Server credentials come from environment variables named in the config (`credential_env`), never from the file.

Each service has its own tests: `cd services/<name> && uv run pytest -q`.

## Gateway contracts

**MCP endpoint**: `POST /mcp`, Streamable HTTP, protocol 2026-07-28 and the older session-based versions.
Send `Authorization: Bearer <credential>` (or `X-API-Key`). Two kinds of credential work:

- an API key (`urumi_...`), created on the dashboard. Calls are audited with `via = mcp`.
- a chat token (`urumichat_...`), from `POST /api/auth/chat-token`. Calls are audited with `via = chat`.
  Optional header `X-Urumi-Chat-Id: <conversation id>` labels the audit rows.

The credential is checked on every HTTP request. A revoked key, a deactivated user, or a logged-out session fails on its next call, including over a connection that is already open.

Tool names are `<server slug>__<tool>`, e.g. `woo__list_orders`, `supplier__list_products`.

Refusals come back as tool results with `isError: true` and a plain sentence, not as protocol errors: unknown tool, server disabled, role not allowed, upstream unreachable, upstream timed out, upstream rejected the stored credential. A timeout on a tool that can change data says `OUTCOME UNKNOWN`.

**Dashboard API**: `/api/...`, cookie session plus header `X-Urumi-CSRF: 1` on writes. See `/api/docs`.

**Chat** (in the cluster): Ingress sends `/api/chat` to `chat-agent`, the rest of `/api` and `/mcp` to `gateway`. The dashboard gets a chat token from the gateway, then calls `/api/chat` with `Authorization: Bearer <chat token>`. The chat service forwards it to `/mcp`, with `X-Urumi-Chat-Id` so the audit rows carry the conversation id.

**Approval gate**: a tool without `readOnlyHint: true` is held before the gateway call. The stream sends `approval_required`; the dashboard answers with `POST /api/chat/approve` (`approve` or `deny`); the stream continues with `approval_decided` and the `tool_result`. A denied call never reaches the gateway. Details in `chat-agent/README.md`.

**Who is asking**: a server can opt in (`forward_identity`, the "Tell this server who is asking" switch on the Servers page) to receive the caller's display name in an `X-Urumi-User` header, percent-encoded. Never the email. Off by default. Slack uses it to post as `Bob (via urumi)`. A header is not proof of anything on its own: it is safe here because only the gateway can reach those servers (bearer token plus NetworkPolicy).

**Dashboard helpers on the gateway**: `POST /api/servers/test` lists an upstream's tools without saving it (and names base-name collisions with other servers), `GET /api/audit` takes `after_id` (live polling) and `chat_id` (one run), and `GET /api/audit/summary?hours=24` gives per-server calls, error rate and p50/p95 for the Overview page.

## WooCommerce connection

`woo-mcp` sends requests to `WOO_API_URL` (the WordPress Service inside the cluster) and signs them for `WOO_STORE_URL` (WordPress's configured home URL). WooCommerce rebuilds the signed URL from its home URL's host and port, which must match exactly, and from the scheme the request actually arrived on. Inside the cluster that is plain HTTP, even when the public store is HTTPS. So for `https://shop.example.com` the chart sets `WOO_STORE_URL=http://shop.example.com` and `WOO_AUTH=oauth1`. WooCommerce accepts Basic auth (`WOO_AUTH=basic`) only on requests that arrive over HTTPS.

| Variable | Example |
|---|---|
| `WOO_STORE_URL` | `http://shop.urumi.test:18080` |
| `WOO_API_URL` | `http://urumi-wordpress.urumi-store.svc.cluster.local` |
| `WOO_CONSUMER_KEY` / `WOO_CONSUMER_SECRET` | from WooCommerce → Settings → Advanced → REST API, Read/Write |
