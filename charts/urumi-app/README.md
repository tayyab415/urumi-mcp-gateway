# App Helm chart

This chart runs everything except the store: the MCP gateway, the three MCP
servers behind it, the chat agent and the dashboard. The store chart
(`charts/urumi-store`) runs WordPress and MariaDB in its own namespace.

```text
                 Traefik (Ingress), one hostname (app.urumi.test locally)
          ┌──────────────┬───────────────┬───────────────┐
          │ /            │ /api, /mcp    │ /api/chat     │
          ▼              ▼               ▼               │
     dashboard        gateway ◀──────── chat ───▶ Bedrock (HTTPS)
     (static)       (SQLite on a       (user's chat token)
                      volume)
          ┌──────────────┼───────────────┐
          ▼              ▼               ▼
       woo-mcp        shipping        supplier        namespace urumi-app
          │
──────────┼──────────────────────────────────────────────────────────────
          ▼
      WordPress ── MariaDB                             namespace urumi-store
```

## The pieces

| Workload | Port | Holds | Replicas |
|---|---|---|---|
| `urumi-gateway` | 8080 | users, keys, servers, sealed upstream credentials, audit log, on a 1 GiB volume | 1, `Recreate` |
| `urumi-woo-mcp` | 8000 | the WooCommerce REST key | 1 (stateless) |
| `urumi-shipping`, `urumi-supplier` | 8000 | the credential each one checks | 1 each |
| `urumi-slack-mcp` (optional) | 8000 | the Slack bot token, and the token the gateway presents to it | 1 (stateless) |
| `urumi-chat` | 8000 | conversations and pending approvals, in memory | 1 |
| `urumi-dashboard` | 8080 | static files | 1 |

The gateway runs one replica because SQLite allows one writer and the volume
mounts on one node. `Recreate` stops the old Pod before the new one starts, so
the two never share the database file.

On its first start the gateway creates the admin from the Secret and registers
the servers in `gateway.bootstrapServers` (WooCommerce, shipping, supplier).
Both happen only on an empty database. After that the dashboard is the source
of truth: a removed server stays removed across restarts and upgrades. The
server list is a ConfigMap with Service URLs and timeouts; each credential is
named by an env var (`credential_env`) that comes from the Secret.

## Public and internal

| Reachable from | Gateway | Chat | Dashboard | woo-mcp, shipping, supplier | WordPress REST |
|---|---|---|---|---|---|
| The internet, through Traefik | `/api`, `/mcp`, OAuth paths | `/api/chat` | `/` | no route | storefront only |
| Inside the cluster | from chat | — | — | from the gateway only | from woo-mcp |

Ingress gives the outside world a path to three Services and nothing else. The
NetworkPolicies in `templates/networkpolicy.yaml` decide who may connect inside
the cluster. The namespace starts closed (no connection in or out) and each
policy opens one path:

- Traefik may connect to the gateway, chat and the dashboard.
- Only the gateway may connect to the three MCP servers. `woo-mcp` checks no
  credential of its own, so this policy is what keeps everything else off it.
- `woo-mcp` may connect to the WordPress Pods and nothing else.
- Chat may connect to the gateway and to HTTPS outside the cluster (Bedrock).
- The gateway may connect to the MCP servers and to public addresses, for
  servers an admin adds by URL. Private ranges and cloud metadata stay closed.
- With TLS on, Traefik may also connect to cert-manager's short-lived "solver"
  Pod, which proves to Let's Encrypt that we control the hostname.

The `urumi-app` namespace also enforces the "restricted" Pod Security Standard.
Kubernetes refuses any Pod there that could run as root, escalate privileges or
keep Linux capabilities. Every container runs as a fixed non-root user with a
read-only root filesystem and a writable `/tmp`.

## On the internet

The VM serves the app at **https://app.34-67-189-86.sslip.io** and the store
at **https://shop.34-67-189-86.sslip.io**. A request gets in like this:

```text
browser, Claude Code or Cursor
   │  DNS: sslip.io answers any name with an IP in it with that IP
   ▼
34.67.189.86, a static IP ── GCP firewall rule urumi-allow-web: TCP 80 and 443 only
   ▼
k3d's load balancer container, on the VM's ports 80 and 443
   ▼
Traefik: picks an Ingress by hostname and path, ends TLS, runs the middlewares
   ▼
gateway, chat or dashboard (WordPress, for the shop hostname)
```

- **Certificates.** cert-manager (`bash scripts/gcp-vm.sh install-cert-manager`)
  watches for Ingresses with the `cert-manager.io/cluster-issuer` annotation.
  It asks Let's Encrypt for a certificate, proves it controls the hostname by
  serving a token over plain HTTP, and keeps the certificate in the
  `urumi-app-tls` Secret. It renews it a month before the 90-day expiry.
- **HTTPS only.** Plain HTTP gets a 301 redirect to HTTPS. The login cookie is
  `Secure` (sent only over HTTPS) and `HttpOnly` (page scripts cannot read it).
- **Five Ingresses, one hostname.** Traefik attaches middlewares per Ingress,
  so login, OAuth sign-in, `/mcp` and chat each have their own Ingress and
  their own limit. Only the main one carries the cert-manager annotation; all
  five use its certificate.
- **OAuth paths go to the gateway.** `/.well-known/oauth-authorization-server`
  and `/.well-known/oauth-protected-resource` are on the main Ingress.
  `/authorize`, `/token`, `/register`, `/revoke` and `/oauth` have their own
  Ingress and use the login limit. Without these routes `/` would answer them
  with the dashboard's HTML and an MCP client could not find the sign-in.
- **Rate limits.** Traefik counts requests before they reach a Pod and answers
  429 past the limit (`ingress.traefik.rateLimits`):

| Path | Limit | Counted per |
|---|---|---|
| `/api/auth/login`, `/authorize`, `/token`, `/register`, `/revoke`, `/oauth` | 10 at once, then 20 a minute | client address (one shared count) |
| `/mcp` | 50 at once, then 10 a second | `Authorization` header: each API key |
| `/api/chat` | 20 at once, then 30 a minute | `Authorization` header: each chat session |

k3d's load balancer hides the caller's address: every request reaches Traefik
from `10.42.0.1`. So on this VM the login limit is one limit for everybody.
That still stops password guessing, but a stranger could use it to slow down
everyone's sign-in for a minute. On a cluster whose load balancer keeps client
addresses (`externalTrafficPolicy: Local`), the same middleware counts each
address separately. The per-key limits do not depend on addresses.

`woo-mcp` still calls WordPress over plain HTTP inside the cluster, so
WordPress checks each signature against `http://shop…`, not `https://`. The
chart signs that form (see `templates/woo-mcp.yaml`).

## The Secret

`python3 scripts/create-app-secret.py` writes `.runtime/app/secrets.json`
(mode 600, outside Git). It never prints a value.

| Key | Used by | Notes |
|---|---|---|
| `gateway-secret-key` | gateway | Seals upstream credentials. Never rotated by the script: changing it makes stored credentials unreadable. |
| `admin-password` | gateway | The first admin's password, used once on an empty database. |
| `shipping-api-token`, `supplier-api-key` | gateway and each server | The gateway sends them; each server checks its own. |
| `woo-consumer-key`, `woo-consumer-secret` | woo-mcp | Copied from the store's Secret on every run, so both sides agree. |
| `bedrock-api-key` | chat | A Bedrock API key for the IAM user `urumi-chat-bedrock`, which may call Kimi K3 and nothing else (see `services/chat-agent/README.md`). It expires 30 days after creation. Optional: without it the chart installs and chat reports a model error. `aws-access-key-id` + `aws-secret-access-key` also work, but are long-lived. |

Pods read the Secret when they start. `helm upgrade` replaces Pods whose image
or settings changed; after a change to the Secret alone, run `kubectl rollout
restart deployment -n urumi-app` so running Pods pick it up.

Revoke the Bedrock key with `aws iam list-service-specific-credentials --user-name
urumi-chat-bedrock` and then `aws iam delete-service-specific-credential
--user-name urumi-chat-bedrock --service-specific-credential-id <id>`. A new key
from `aws iam create-service-specific-credential --user-name urumi-chat-bedrock
--service-name bedrock.amazonaws.com --credential-age-days 30` goes into the
Secret file's `bedrock-api-key`; then run `deploy-app`.

## Images

There is no registry. `bash scripts/gcp-vm.sh build-images` builds the five
images on the VM, imports them into k3d, and writes `.runtime/app/images.yaml`.
Each tag is the first 12 characters of a hash of that image's source, so a code
change produces a new tag and `helm upgrade` replaces exactly the Pods whose
code changed. The chart refuses to render with an empty tag.

## Install

On the GCP application VM, after the store is deployed:

```bash
python3 scripts/create-app-secret.py
bash scripts/gcp-vm.sh build-images
bash scripts/gcp-vm.sh deploy-app
```

`deploy-app` creates the namespace with its Pod Security label, applies the
Secret over SSH stdin, copies the chart and runs `helm upgrade --install` with
`values-local.yaml`, `images.yaml` and `.runtime/app/values-vm.yaml`. Extra
arguments pass through to Helm.

Check without installing:

```bash
bash scripts/helm.sh lint --strict charts/urumi-app \
  -f charts/urumi-app/values-local.yaml -f .runtime/app/images.yaml
```

## What changes between local and production

Everything that differs is a value. `values-prod.yaml` is an example:

| Value | Local (k3d) | The GCP VM (`.runtime/app/values-vm.yaml`) | Production |
|---|---|---|---|
| `host`, `publicUrl` | `app.urumi.test`, port 18080 | `app.34-67-189-86.sslip.io`, `https://` | the real hostname, `https://` |
| `ingress.annotations` | none | `cert-manager.io/cluster-issuer: letsencrypt` | the same, or your own certificate |
| `ingress.tls` | off | on, Secret `urumi-app-tls` | on |
| `gateway.cookieSecure` | false | true | true |
| `store.publicUrl` | `http://shop.urumi.test:18080` | `https://shop.34-67-189-86.sslip.io` | the store's real URL |
| `gateway.storage.storageClassName` | `local-path` | `local-path` | the cluster's StorageClass |

## Checked live on the GCP VM, 2026-10-25

- Install: all six Deployments became ready within a minute. The gateway log
  showed the admin created and `woo, shipping, supplier` registered.
- Through Traefik, as an outside client would: the dashboard served, the admin
  logged in, all three servers were `ok` (5, 2 and 3 tools), and an API key
  listed all 10 tools at `/mcp`. `woo__list_orders` returned order 44 from the
  real store. The audit log recorded the call under the admin, `via mcp`.
- NetworkPolicy: a Pod in the `default` namespace could not connect to
  `woo-mcp`, shipping or the gateway. From inside the chat Pod, the gateway
  answered 200 while `woo-mcp` and WordPress refused the connection.
- Persistence: after the gateway Pod was deleted and replaced, the API key,
  the audit row and the three servers were all still there.

## Checked from the internet, 2026-10-26

From the coding workspace, which is outside the VM, against the public URL:

- Let's Encrypt issued both certificates within a minute (`urumi-app-tls`,
  `urumi-store-tls`), and the clients checked them as normal.
- `http://…/mcp` answered 301 to `https://…/mcp`. The dashboard served.
- Admin login set a cookie marked `Secure` and `HttpOnly`. All four servers
  were `ok` (woo 5 tools, shipping 2, supplier 3, slack 3).
- An MCP client with a fresh API key listed 13 tools at `/mcp`;
  `woo__list_orders` returned order 44. The audit log recorded it under the
  admin, `via mcp`. The key was then revoked.
- Chat (Kimi K3): "What orders came in today?" called `woo__list_orders`. A
  "create a product" turn was held for approval, denied, and never ran.
- Claude Code 2.1.308, as member Bob with his API key (`claude -p
  --mcp-config`), connected to `/mcp`. In one prompt it called
  `shipping__quote_shipping` and `supplier__list_products`. The audit log
  recorded all four calls under Bob, `via mcp`, with his key's prefix.
- Rate limits: 80 parallel `/mcp` requests with one key got 67 through and 13
  answered 429; a different key went straight through. Fifteen quick wrong
  passwords got ten 401s, then 429s, with one more 401 as the allowance
  refilled.

## Slack (optional)

`slack.enabled: true` adds `urumi-slack-mcp`: three tools (`channel_info`, `read_messages`, `post_message`) on
one Slack channel, with one shared bot token. People never need Slack accounts. The gateway's roles decide who
may read and who may post. Off by default, so a machine without Slack still installs.

To turn it on:

1. Make the Slack app, channel and token (`infra/slack/README.md`). The token lives in `~/.urumi/slack.env`.
2. `python3 scripts/create-app-secret.py` copies it into the Secret as `slack-bot-token`, and makes the
   `slack-mcp-token` the gateway uses to call the server.
3. Put `slack: {enabled: true, channelId: C...}` in the machine's values file (`.runtime/app/values-vm.yaml`).
4. `bash scripts/gcp-vm.sh build-images`, then `deploy-app`.
5. A gateway whose database already has servers registers only on an empty database, so add Slack to it once
   through the API: `bash scripts/gcp-vm.sh kube exec -n urumi-app deploy/urumi-gateway -- .venv/bin/python -m gateway.seed --url http://127.0.0.1:8080 --config /etc/urumi/seed.json`
   (it skips servers that already exist). A fresh install needs no such step.

Slack is registered with "Tell this server who is asking" on, so posts show `Name (via urumi)`. A gateway
database from before that setting existed gets the new column added when the gateway starts. Registered
servers keep their saved setting, so switch it on for Slack on the Servers page (or `PATCH /api/servers/{id}`
with `{"forward_identity": true}`) on a gateway that already had Slack.

## Seeding the team (optional)

A fresh gateway has the bootstrap admin only. To add Ana, Bob, Chen and Dev as members with an API key each,
run the seeder inside the gateway Pod and copy the passwords out. They are random, never in git, and the file
in the Pod is removed afterwards:

```bash
# team.json: {"users": [{"email": "ana@urumi.test", "name": "Ana", "role": "member"}, ...]}
B64=$(base64 -w0 team.json)
bash scripts/gcp-vm.sh kube -n urumi-app exec deploy/urumi-gateway -- sh -c \
  "echo $B64 | base64 -d > /tmp/team.json && cd /app && .venv/bin/python -m gateway.seed \
   --url http://127.0.0.1:8080 --config /tmp/team.json --credentials-out /tmp/creds.json --keys"
bash scripts/gcp-vm.sh kube -n urumi-app exec deploy/urumi-gateway -- cat /tmp/creds.json > ~/.urumi/vm-team-credentials.json
chmod 600 ~/.urumi/vm-team-credentials.json
bash scripts/gcp-vm.sh kube -n urumi-app exec deploy/urumi-gateway -- rm /tmp/creds.json /tmp/team.json
```

Then `scripts/mcp_cli.py` and `scripts/agent_chat.py` can act as each person against the cluster:
`--gateway https://<host>/mcp --creds ~/.urumi/vm-team-credentials.json --as bob` (and `--chat https://<host>`).
Through the SSH tunnel instead of the public host, add `--host app.urumi.test:18081`.

Network: only the gateway may connect to `urumi-slack-mcp`, and it may open connections to public HTTPS only
(Slack), with every private range closed. Checked on the VM: `slack-mcp` reaches slack.com:443 and nothing
inside the cluster; the gateway reaches `slack-mcp`; chat and `woo-mcp` do not.
