# What changed after the independent review

On 2026-10-26 a separate agent reviewed the whole project and tried to break
it. It had no part in building it. Its report is
[astra-review.md](astra-review.md), with its scripts beside it and its proof
in [proof/](proof/). It confirmed 11 findings: 7 Critical, 2 High, 2 Medium.

All 11 were fixed the same day and deployed (app Helm revision 19). Each fix
has a test. Each was then checked again on the live system, or with the
review's own reproduction script where it cannot be run live.

| ID | Finding | Fix | Checked after the fix |
|---|---|---|---|
| R01 | The provisioner could weaken a store namespace's Pod Security label and submit privileged workloads | The admission policy now requires `baseline` or `restricted` when a store namespace is created and refuses lowering it. It also refuses workloads that use the host's process, IPC or network namespace, run privileged, or mount host paths. | Live, 17 of 17 server-side dry runs as the provisioner's ServiceAccount: the review's privileged relabel, privileged namespace and hostPID/privileged/hostPath Deployment are refused. The store chart's own workloads are allowed. A real store was created (Ready in 73 s) and deleted. |
| R02 | A managed store's tools added after first discovery skipped admin review | Every server is trusted on first discovery only, store servers included. | `test_pinning.py`, and the review's wire script: the late tool is hidden from the member, and a call to it gets `-32602 Unknown tool`. |
| R03 | The result cap counted text only | The whole result is measured as JSON. Text is cut to fit. Images, audio, resources and structured content that don't fit are left out with a note. Upstream answers are read up to 4 MiB. | `test_result_cap.py` (8 tests). With the review's scripts, a 300,236-byte structured result became 440 bytes with the note, and a 410,489-byte image became 367 bytes with the note. |
| R04 | A gateway crash during a write left no audit row | The audit row is written as `pending` before the call is sent; if that write fails, the call is refused. On start, leftover pending rows become `interrupted`. | `test_audit.py` kills a real gateway process mid-call. The review's crash script now finds 1 row where it found 0. |
| R05 | Search-mode calls were not fully audited, and the MCP filter hid them | One audit row per `tools/call`, the search tools included. The `mcp` filter also shows `mcp-search` rows. | `test_toolsearch.py`, and a live search call. |
| R06 | Rewriting the Authorization header's whitespace got a fresh rate-limit allowance | The gateway keeps its own limit per verified credential (50 at once, then 10 a second), on top of Traefik's. | Live: 300 requests in 14.5 s with one key written four ways; 128 refused with `-32011`, spread across all four spellings. |
| R07 | Retry on a store whose setup Job failed failed again at once | Retry marks the store; the next pass deletes the failed Job and runs Helm again. | `test_api.py`, and the review's provisioner script: `Helm install called: True`, the store back to Provisioning. |
| R08 | A reconciliation already running could overwrite Deleting with Ready | A delete sets an annotation nothing clears, and it outranks the phase. Phase writes re-read the namespace first. | `test_api.py`, and the review's script: the phase stays `Deleting`, and the next pass deletes the namespace. |
| R09 | Behind the Cloud Run proxy, the first store's Storefront button opened the dashboard | `/api/auth/me` returns the first store's address and the store domain from the gateway's settings. | Live, in the browser: the Stores page shows `shop.34-67-189-86.sslip.io`. |
| R10 | The chat's approval card named the target only as "Store" | The gateway sends each tool's server name and slug, and the card shows the whole store name. | `test_agent.py`: "Run store-north__create_product on Store: North". |
| R11 | Unknown tools returned a result instead of a JSON-RPC error | JSON-RPC `-32602 Unknown tool: <name>`, the same answer for tools the caller cannot use, so the error never reveals a tool exists. The audit log still records `denied` or `unknown_tool`. | Live: an unknown tool and an admin-only tool called as a member both get `{"code": -32602, "message": "Unknown tool: …"}`. |

Tests after the fixes: gateway 62, provisioner 18, chat-agent 29 (plus one live
Bedrock test), slack-mcp 14, woo-mcp 10, demo-mcps 3. The dashboard builds.

What the fixes did not change, and the threat model says so:
- The rate-limit counts live in the gateway's memory, so a restart refills them.
- A store server's first discovery is still trusted once, like any server's.
- The review's recordings (`proof/*.mp4`) are not in git, like the other evidence videos.
