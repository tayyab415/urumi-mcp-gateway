# Evidence from testing on the VM (2026-10-26)

Screen recordings (`.mp4`, not in git because of size) and terminal captures
(`.txt`) from the session that finished the assignment. Each file says what
it proves.

| File | What it shows |
|---|---|
| `01-multistore-checkouts.mp4` | A shopper checks out on two freshly provisioned stores (North, East). Both orders are #15: each store has its own database. |
| `02-verify-multistore.txt` | `scripts/verify-multistore.sh north south`: 17/17 checks. Store isolation by NetworkPolicy, gateway-only access to a store's MCP server, the provisioner's permission fence. |
| `03-chat-compares-two-stores.mp4` | The brief's bonus test, part 1: Ana asks the chat agent to compare today's orders across her two stores; it calls both stores' tools; the audit log shows both calls under Ana. |
| `04-delete-store.txt` | The bonus test, part 2: Ana deletes East. Its tools vanish from her list at once, a remembered tool name gets "Unknown tool", and the namespace, volumes and Secret are gone 8 s later. |
| `05-claude-code-oauth.txt` | A real Claude Code browser login (OAuth): calls under `oauth:Claude Code (urumi)` for Bob; an admin disconnects the app and Claude Code needs to sign in again. |
| `06-provisioner-restart.txt` | The provisioner Pod killed 11 s into provisioning a store; the replacement finishes it (Ready at 84 s). |
| `07-dashboard-delete-create-restart.mp4` | The Stores page as Bob: delete a store, create one, watch it go from Provisioning to Ready while the provisioner is restarted. |
| `08-tool-pinning.txt` | A compromised upstream rewrites a tool description into a prompt injection; the gateway holds the tool for everyone; reverting releases it. |
| `09-tool-pinning-held.png`, `09-tool-pinning-admin.mp4` | The admin's Permissions page with the held tool: approved text and new text side by side. |
| `10-dod-user-a-chat.mp4` | Definition of done, user A (admin): store checkout, chat creates a product after approval, the product on the storefront, a shopper orders it, chat lists today's orders. |
| `11-dod-user-b-claude-code.txt` | Definition of done, user B (Bob): Claude Code with only the URL and his key; tools from every enabled server; today's orders; one prompt using two upstreams; key revoked, next call 401. |
| `12-dod-disable-kill-audit.txt`, `13-dod-disable-kill-audit.mp4` | Disable Shipping: its tools disappear from Bob's client and from chat. Kill the supplier Pod: the others keep working, the dashboard shows it down with the reason, then ok. The audit log attributes everything. |
| `14-local-k3d-install.txt` | A fresh k3d cluster with the local values only (`scripts/local-up.sh`): both charts Ready in about 4.5 minutes; an MCP call reaches WordPress. |
| `15-final-smoke.txt` | `scripts/smoke.sh` on the live VM after the last deploy: endpoints, OAuth discovery, each person's tools, the stores, all Pods healthy. |
