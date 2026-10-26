> **Status after the fixes (added by the project owner, 2026-10-26 12:55 UTC).**
> All 11 findings are fixed. The fixes are commits `4bc5320`..`f2518c6`, merged in
> `4f67311` and deployed as app Helm revision 20; R09 was fixed in `4e80dcd`.
> How each was checked:
>
> | ID | Check |
> |---|---|
> | R01 | Live server-side dry runs as the provisioner's ServiceAccount: weakening `store-north` to `privileged`, a new store namespace at `privileged`, and the hostPID/privileged/hostPath Deployment are all refused by the admission policy; a `baseline` store namespace and an ordinary Deployment are still allowed. Nothing was created. |
> | R02 | `gateway_reproductions.py`: a tool added after registration is invisible to the member. `wire_reproductions.py` now stops at "held for review" for the late tool. Test `test_store_servers_get_no_free_pass_for_tools_added_later`. |
> | R03 | `gateway_reproductions.py`: oversized structured content and images now carry the truncation marker. Tests in `test_result_cap.py`, including the upstream byte limit. |
> | R04 | `audit_crash_reproduction.py`: after SIGKILL during a committed write there is now 1 audit row (was 0). Tests in `test_audit.py`. |
> | R05 | Live: Bob's `gateway__search_tools` and `gateway__call_tool` calls appear in the audit log as `mcp-search`, inside the MCP filter. |
> | R06 | Live, 150 requests at once straight to the VM with six whitespace spellings of one key: 24 refused by the gateway's own per-credential limiter (`-32011`). One unchanged header: 82 of 150 refused. |
> | R07, R08 | Tests `test_retrying_after_a_failed_setup_job_runs_setup_again` and `test_a_delete_during_a_reconcile_pass_is_not_undone`. `provisioner_reproduction.py` now reaches the new `delete_failed_setup_job` step (it has no stub for it, so it stops there). |
> | R09 | Live: the Storefront button opens `https://shop.34-67-189-86.sslip.io` from the Cloud Run address. |
> | R10 | Approval card carries the full store name; chat-agent tests. |
> | R11 | Live: an unknown tool and an admin-only tool both return JSON-RPC `-32602` "Unknown tool", and both are audited. |
>
> Test counts after the fixes: gateway 62, provisioner 18, chat-agent 29 (+1 live test skipped), woo-mcp 10, slack-mcp 14, demo-mcps 3; the dashboard builds.

The core is working, but I would not sign off on the submission's security and reliability claims yet. I independently exercised real storefront checkout, approved chat writes, member chat, Claude Code, the Python MCP SDK, OAuth, tenant isolation and recovery on the live system. All 113 service tests passed, with one live-model test skipped, and the dashboard built. I found **11 confirmed findings: 7 Critical, 2 High and 2 Medium**, using the owner's severity definitions, where a false protection claim also counts as Critical. The main failures are a provisioner permission escape, tools added without review, ineffective result and rate limits, missing audit records, and failed provisioning retries. Cloud Run works for login, OAuth, MCP and streaming chat, but breaks the first-store link. Some failure reproductions use the actual application code with isolated fixtures; those are identified below and are not presented as attacks performed on the live stores.

| ID | Severity | Status | Finding | Evidence scope |
|---|---|---|---|---|
| R01 | Critical | CONFIRMED | Provisioner can remove a store namespace's Pod Security restriction and submit privileged workload templates | Live API-server dry runs |
| R02 | Critical | CONFIRMED | Managed store servers can add member-accessible tools without admin review | Actual gateway, HTTP upstream and MCP SDK |
| R03 | Critical | CONFIRMED | Structured and image results bypass the claimed 256,000-character cap | Actual gateway, HTTP upstream and MCP SDK |
| R04 | Critical | CONFIRMED | A committed write can survive a gateway crash with no audit row | Actual gateway code in a killed review subprocess |
| R05 | Critical | CONFIRMED | Search-mode calls bypass auditing; the MCP filter also hides their recorded inner calls | Live gateway and audit API |
| R06 | Critical | CONFIRMED | One API key gets new rate-limit buckets by changing Authorization whitespace | Live HTTP requests |
| R07 | Critical | CONFIRMED | Retrying a failed setup Job immediately fails again instead of restarting setup | Actual provisioner with recorded Kubernetes-state fixtures |
| R08 | High | CONFIRMED | A reconciliation in progress can overwrite a store deletion request | Actual provisioner with a controlled concurrent fixture |
| R09 | High | CONFIRMED | Cloud Run's first-store Storefront button opens the dashboard | Live browser |
| R10 | Medium | CONFIRMED | Store write approval loses the target store's name | Live browser and source |
| R11 | Medium | CONFIRMED | Unknown tools return an execution result instead of a JSON-RPC protocol error | Live HTTP requests and current MCP specification |

## Scope and evidence

Reviewed on 2026-10-26 UTC. The original eight-page PDF is the authority; its extracted text is in [original-brief.txt](proof/original-brief.txt). I read the README, design, threat model, multi-store documentation and the claimed evidence before testing.

The review began at `https://app.34-67-189-86.sslip.io`. After the owner's planned Cloud Run change, I also tested `https://urumi-1027824348124.us-central1.run.app`. The planned Helm upgrade, its gateway restart, the changed OAuth issuer/audience and Cloud Run's known `/healthz` 404 are excluded from findings. Review keys created before the upgrade continued working at both URLs. Fresh OAuth discovery and PKCE exchanges use the new audience correctly.

The scripts beside this report are review-only reproductions. Live scripts run through an ephemeral loopback process that keeps credentials in memory and redacts captured output. Its credentials were not saved in the scripts or proof. Fixture scripts create their own local gateway/upstream or mock Kubernetes observations; they do not change live store workloads.

The source references below describe the reviewed checkout. Numbered excerpts are saved in [source-findings.txt](proof/source-findings.txt). The owner was making the announced Cloud Run changes during the review; I did not edit implementation files.

## Findings

### R01 — Provisioner can weaken Pod Security and submit privileged workloads

**Critical · CONFIRMED · live admission checks**

The component that creates stores can remove the rule that stops store containers from accessing the Kubernetes machine.

**Where:** `charts/urumi-app/templates/provisioner.yaml:26–37,119–128`. The provisioner has namespace `patch` and `update`, and its admission policy checks the store label but does not preserve Pod Security labels. Its tenant role permits workload creation in store namespaces.

**Reproduce:** while an authorized review store exists, run:

```bash
bash docs/review/rbac-dryrun.sh
bash docs/review/rbac-deployment-dryrun.sh
```

The first script impersonates the **provisioner's actual ServiceAccount**, submits a server-side dry-run patch changing the review namespace from `baseline` to `privileged`, and dry-runs a new store-labelled namespace with `privileged` enforcement. Both succeed. The second dry-runs a Deployment with `hostPID: true`, a privileged root container and a mount of the host's `/` directory.

**Proof:** [rbac-podsecurity-bypass.txt](proof/rbac-podsecurity-bypass.txt):

```text
"pod-security.kubernetes.io/enforce":"privileged"
namespace/store-review-policy-probe
Warning: would violate PodSecurity "restricted:latest": ...
deployment.apps/review-privilege-proof
baseline
```

The warning does not reject the Deployment. Pod Security normally rejects the eventual Pod under `baseline`; the separately accepted namespace patch removes that enforcement. The final `baseline` is a read of the unchanged live namespace. No privileged Pod was started, no label was actually changed, and the dry-run resources did not exist afterward.

**Impact:** compromise of the provisioner gives a route around the store workload security boundary, with potential host access and consequent access to other tenants. The ordinary NetworkPolicy tests all passed; they do not prevent a privileged container from reaching the host. This is stronger access than the documented store-only permission fence suggests.

**Fix:** admission must require safe Pod Security labels on namespace creation and prevent their weakening on update. Limit the namespace name/owner invariants too. Validate workload templates against the allowed store workload shape, including host namespaces, privileged mode and hostPath. Keeping the labels immutable is the minimum fix. Add a negative API-server test for these exact requests.

### R02 — Tool pinning trusts every new tool from a managed store

**Critical · CONFIRMED · isolated real HTTP/MCP reproduction**

**Where:** `services/gateway/gateway/core.py:178–184,199–202`. `trust_new` stays true whenever `managed_by` is set, even after the first successful discovery. A newly discovered tool therefore receives an approved definition and roles derived from the upstream's own hints.

**Reproduce:**

```bash
services/gateway/.venv/bin/python docs/review/wire_reproductions.py
```

The script starts an HTTP MCP upstream and the actual gateway, registers the upstream as a provisioner-managed store owned by a member, and discovers one tool. It then adds a new read-only-hinted tool, refreshes the server, and lists and calls it as that member without an admin approval.

**Proof:** [wire-confirmed.txt](proof/wire-confirmed.txt), trimmed:

```text
Initial tools: ['store-fixture__payload']
Added upstream tool after first discovery, no admin approval. Member list:
['store-fixture__late_tool', 'store-fixture__payload']
Member called unreviewed late tool: isError False
```

A separately tested manually registered server correctly hid its late tool: [gateway-reproductions.txt](proof/gateway-reproductions.txt).

**Impact:** a compromised store MCP server can introduce a new description and operation into its owner's tool list without review, and can obtain member access by claiming `readOnlyHint`. Existing changed definitions still get held; this bug concerns **new** tools on managed servers. It contradicts the unqualified “or a new tool” pinning claim in `docs/video-facts.md` and `docs/threat-model.md:145–152`.

**Fix:** trust initial discovery once, including for managed stores, then hold all later additions. Provisioning should explicitly approve the known initial WooCommerce catalog rather than granting permanent trust to whatever the server adds.

### R03 — Result size protection skips structured content and images

**Critical · CONFIRMED · isolated real HTTP/MCP reproduction**

**Where:** `services/gateway/gateway/core.py:398–415`. `_capped` counts only `TextContent`. If no text was truncated, it returns before checking `structuredContent`; image and other content sizes are never counted.

**Reproduce:**

```bash
services/gateway/.venv/bin/python docs/review/wire_reproductions.py
```

The upstream returns either two text characters plus a 300,000-character structured value, or a valid generated PNG whose base64 representation exceeds 400,000 characters. The real SDK receives both through the gateway.

**Proof:** [wire-confirmed.txt](proof/wire-confirmed.txt):

```text
Wire result structured bytes 300236 isError False ... text ['ok']
Wire result image bytes 410489 isError False ... text []
```

Neither response gets a truncation marker. A plain-text control is truncated correctly: [gateway-reproductions.txt](proof/gateway-reproductions.txt).

**Impact:** clients receive oversized payloads despite the documented result limit. A hostile upstream can consume gateway/client memory and model input capacity. I demonstrated the cap bypass, not an out-of-memory crash. Whether a particular chat path consumes each content type depends on its adapter.

**Fix:** enforce a budget over the whole response, including structured data, image/audio data, embedded resources and metadata. Apply it before returning even when text is short. Also bound upstream HTTP response bytes before fully buffering them. A text-only cap is not the claimed result cap.

### R04 — A gateway crash loses the audit record of a completed write

**Critical · CONFIRMED · actual code in an isolated subprocess**

**Where:** `services/gateway/gateway/core.py:268–296,366–372`. The audit event is committed only after the upstream returns. The cancellation shield protects ordinary cancellation, not process death. Audit persistence failures are also logged and swallowed.

**Reproduce:**

```bash
PYTHONPATH=services/gateway services/gateway/.venv/bin/python \
  docs/review/audit_crash_reproduction.py
```

The script runs the actual gateway call path against a fixture write that commits a marker and then waits before replying. It checks SQLite, kills only its own child process, and checks the write and audit database again.

**Proof:** [audit-crash-confirmed.txt](proof/audit-crash-confirmed.txt):

```text
While write has committed: audit rows 0
After gateway process SIGKILL: write marker exists True audit rows 0
Child exit -9 Only the review-created subprocess was killed.
```

**Impact:** an order/product/message write can happen with no durable gateway record of who requested it. This breaks “every tool call” in the brief and the code's claim to record crashed calls. Retrying the unknown result can also duplicate a write. I did not kill a live gateway during a real customer write.

**Fix:** durably insert a pending attempt before dispatch, then finalize it with outcome and duration. On restart, mark unfinished attempts as interrupted/unknown rather than pretending they never happened. Refuse writes when the initial audit insert cannot be committed. Use operation identifiers and upstream idempotency or reconciliation where supported.

### R05 — Search tools are not fully audited, and their inner calls vanish from the MCP filter

**Critical · CONFIRMED · live**

**Where:** `services/gateway/gateway/mcp_endpoint.py:61–69`; `gateway/toolsearch.py:119–158`; `gateway/api.py:704,719–720`.

**Reproduce:** with a review key, call `/mcp/search` three times:

1. `gateway__search_tools` with a valid query.
2. `gateway__call_tool` for `woo__list_products`.
3. `gateway__call_tool` with a forbidden meta-tool target.

Read the new audit rows, then request the same rows with `via=mcp`. The runnable live test is `search_audit_confirm.py`, executed by the credential runner.

**Proof:** [search-audit-confirmed.txt](proof/search-audit-confirmed.txt):

```text
Call gateway__search_tools: HTTP 200, isError false
Call gateway__call_tool: HTTP 200, isError false
Call gateway__call_tool: HTTP 200, isError true
New rows after three calls:
  woo__list_products | via mcp-search | outcome ok
New rows with MCP clients filter: []
```

`search()` never reaches the audit writer. Early wrapper errors also bypass it. The inner operation is recorded as `mcp-search`, while the API accepts only `mcp` or `chat` and compares `via` literally.

**Impact:** discovery and refused wrapper attempts leave no audit trace. Even the recorded inner operation is excluded by “MCP clients only.” The brief requires every tool call to be logged, and the Connect/video documentation promises the same audit path for search mode.

**Fix:** audit gateway meta-tools and their denials centrally. Link the wrapper and inner operation so they can be understood without double-counting. Store transport (`mcp`/`chat`) separately from tool mode, or include search variants in the MCP filter.

### R06 — Authorization whitespace bypasses the per-key rate limit

**Critical · CONFIRMED · live**

**Where:** `charts/urumi-app/templates/traefik-middlewares.yaml:29–36`; `services/gateway/gateway/mcp_endpoint.py:147–152`. Traefik uses the raw Authorization header as its rate bucket, while gateway authentication strips whitespace from the token.

**Reproduce:** use two fresh review keys for the comparison. Send 100 concurrent harmless MCP requests with one fixed Authorization representation. With the second key, send 100 requests representing the **same key** with one through 100 spaces between `Bearer` and the token. `rate_variants.py` runs `rate_variants_client.py`; credentials are passed through process input rather than files or command-line arguments.

**Proof:** [rate-variants-confirmed.txt](proof/rate-variants-confirmed.txt):

```text
100 requests, one key and identical header:
  {200: 53, 429: 47} seconds 1.391
100 requests, one key and different whitespace in header:
  {200: 100} seconds 2.301
```

With a burst of 50 and replenishment of 10/second, one normalized key cannot legitimately admit 100 requests in 2.301 seconds. Authentication accepted all representations as the same user.

**Impact:** one stolen or abusive key can evade the claimed per-key protection. This falsifies the per-key limit described in `docs/threat-model.md:89` and the scaling claim; it is not merely the documented ability to use a separate key's separate allowance.

**Fix:** enforce the limit after authentication, keyed by the verified user or stable key ID/hash. If an ingress bucket is retained, put an additional verified-identity limit inside the gateway. Do not rely on raw bearer-header text as credential identity.

### R07 — Failed setup retries do not restart the failed Job

**Critical · CONFIRMED · actual provisioner with state fixtures**

A store whose setup Job failed can stay broken even after its owner presses Retry.

**Where:** `services/provisioner/provisioner/app.py:181–187,258–269`; `docs/multi-store.md:87–89`.

**Reproduce:**

```bash
PYTHONPATH=services/provisioner services/provisioner/.venv/bin/python \
  docs/review/provisioner_reproduction.py
```

The fixture represents a store with healthy database/WordPress/MCP, a Helm release marked `deployed`, and a setup Job that exhausted retries after a temporary download outage. It calls the real retry function and real reconciliation function.

**Proof:** [failed-retry-confirmed.txt](proof/failed-retry-confirmed.txt):

```text
Retry API: 200 Provisioning
After actual reconcile: Failed
  Store setup failed: BackoffLimitExceeded: temporary plugin download outage
Helm install called: False
```

Retry resets annotations and the deadline, but leaves the failed Job. `_ensure_release` accepts the existing deployed release, and `observe` reports the same terminal Job failure.

**Impact:** Retry does not recover this ordinary failure state. The owner needs manual Kubernetes intervention, contrary to the explicit “for a Failed one it starts again” claim. The happy-path restart test passed and does not cover this case.

**Fix:** reconcile failed setup explicitly: recreate the setup Job, or perform an idempotent release/setup retry that preserves credentials and volumes. Distinguish infrastructure readiness, Helm state and setup completion. Add this failed-Job state to retry tests. A live reproduction requiring a Helm upgrade/uninstall was not performed because those actions were forbidden.

### R08 — In-progress provisioning can overwrite Deleting with Ready

**High · CONFIRMED · actual provisioner with concurrent state fixture**

A delete request made while a store is being provisioned can be forgotten, leaving the store and its data running.

**Where:** `services/provisioner/provisioner/app.py:140–170,272–281`.

**Reproduce:**

```bash
PYTHONPATH=services/provisioner services/provisioner/.venv/bin/python \
  docs/review/provisioner_delete_race.py
```

The test pauses the real reconciliation function during its Helm install, calls the real delete function, then lets the earlier reconciliation finish with ready observations.

**Proof:** [provisioner-delete-race.txt](proof/provisioner-delete-race.txt):

```text
Delete API response: Deleting
Namespace phase immediately after delete: Deleting
Namespace phase when prior reconciliation finishes: Ready
Namespace deletion called: False
```

**Impact:** the durable delete intent is overwritten by a stale reconciliation snapshot. Later reconciliations see Ready and have no deletion request to perform. This affects the bonus's clean teardown and leaves allocated resources behind. Deleting the review's already-Ready store worked live; I did not force this race on the cluster.

**Fix:** make Deleting a monotonic intent that reconciliation cannot overwrite. Re-read state after awaited external operations, use resource-version/CAS checks, and serialize delete with provisioning for each store. Persist deletion intent separately if phase observations can change.

### R09 — Cloud Run's first-store link opens the dashboard

**High · CONFIRMED · live browser**

**Where:** `dashboard/src/pages/Stores.tsx:41,72,80`; `https://urumi-1027824348124.us-central1.run.app/stores`.

**Reproduce:** sign in through Cloud Run, open Stores, and press the first “Storefront” button under Urumi Sports.

**Proof:** [cloud-run-browser.txt](proof/cloud-run-browser.txt) and [cloud-run-wrong-storefront.png](proof/cloud-run-wrong-storefront.png):

```text
Displayed first-store host: urumi-1027824348124.us-central1.run.app
Button opened: https://urumi-1027824348124.us-central1.run.app/
New tab title: Urumi
New tab heading: Overview
```

**Impact:** a person cannot reach the first shop through this dashboard action at the owner's new public URL. The real shop at `https://shop.34-67-189-86.sslip.io` remains healthy.

**Fix:** return the configured first-store public URL from the backend and use it directly. Do not derive independent service domains by replacing `app.` in the browser hostname. The create-store address hint uses the same hostname assumption and should also use the configured provisioning domain.

### R10 — Approval screen names the target only as “Store”

**Medium · CONFIRMED · live browser**

**Where:** `services/chat-agent/chat_agent/gateway.py:177–182`; `dashboard/src/state/chat.tsx:133–138`.

**Reproduce:** as admin, ask chat to create a product in a provisioned store and inspect the held approval before approving.

**Proof:** [admin-held-approval.png](proof/admin-held-approval.png), [admin-product-approval.mp4](proof/admin-product-approval.mp4):

```text
Requested: create Review Practice Ball in Review Astra
Approval: Create a new product called Review Practice Ball on Store.
```

The gateway title contains a store name with a colon, such as `Store: Review Astra: Create product`. `_summary` takes the text before the first colon. The dashboard then reparses that summary instead of using the structured server identity.

**Impact:** the decision card omits which of the admin's several stores will be changed. The tool did wait for approval, and the approved write reached the correct store; this is a loss of decision context, not an approval bypass.

**Fix:** send and render the structured server slug/display name. Preserve the complete store prefix when separating a tool title from its server name, and show the target store alongside the argument diff.

### R11 — Unknown tools use the wrong MCP error channel

**Medium · CONFIRMED · live and specification checked**

**Where:** `services/gateway/gateway/core.py:300–315`; both public `/mcp` endpoints.

**Reproduce:** call `tools/call` for `review__no_such_tool` with a valid key.

**Proof:** [final-live-state.txt](proof/final-live-state.txt):

```json
{"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"Unknown tool 'review__no_such_tool'.","type":"text"}],"isError":true}}
```

The [2026-07-28 MCP tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#error-handling) classifies unknown tools as protocol errors and illustrates JSON-RPC `error` code `-32602`. The fetched section is saved in [mcp-tools-spec-2026-error.txt](proof/mcp-tools-spec-2026-error.txt). The earlier 2025-11-25 specification makes the same distinction. HTTP 200 alone is not the issue; the JSON-RPC response channel is.

**Impact:** protocol clients cannot distinguish an unresolved tool request from a tool that ran and failed. This is a concrete protocol conformance defect, although the tested SDK and Claude Code handled normal calls successfully.

**Fix:** raise an MCP/JSON-RPC invalid-parameters error for unknown tools while preserving the audit record and opaque response for inaccessible tools. Continue using `isError` for execution, argument-value and business failures.

## Brief compliance

“Met” means the inspected implementation and independent evidence support the requirement. “Partly met” includes a demonstrated exception or a material verification limit. Existing owner evidence is labelled as such and is not substituted for an independent rerun.

### Mandatory requirements and user stories

| PDF requirement | Result | Proof or limitation |
|---|---|---|
| Four layers: store, own Woo MCP, gateway/React dashboard, chat agent | Met | Source in the four service/UI directories; live [cluster-baseline.txt](proof/cluster-baseline.txt), browser and MCP results |
| Everything runs in Kubernetes | Met | All application workloads and stateful store workloads present in cluster; Cloud Run is an additional proxy |
| Local Kubernetes support | Partly met | Local values/script reviewed and charts rendered. Owner's `docs/evidence/14-local-k3d-install.txt` claims a fresh install; this review did not create a second cluster |
| Same Helm charts on a production-like VPS, configuration differences only | Met | Live VM runs these charts; local/prod template checks; [chart-validation-with-images.txt](proof/chart-validation-with-images.txt) |
| Helm mandatory; local/prod values supplied | Met | Both chart directories and both sets of values exist; no Kustomize dependency |
| Persistent database and WordPress content | Met | Bound PVCs in live namespaces, [cluster-baseline.txt](proof/cluster-baseline.txt). Destructive database/WordPress restart persistence was not rerun |
| Stable storefront/dashboard Ingress; local domain approach documented | Partly met | Live Ingress and first-store TLS work; local `*.urumi.test` hosts/port documented. New dashboard store link fails, R09 |
| Readiness/liveness checks, requests and limits on workloads | Met for running services | Rendered/live workloads contain probes and resource budgets. Finite setup Jobs use completion/deadline rather than serving readiness |
| No hardcoded live secrets; credentials in Kubernetes Secrets | Met in inspected paths | Secret refs and ignored local credential files; API key hashes and credential encryption code. No live secret value was captured |
| Admin sees server status and exposed tools | Met | Overview/Servers pages, live supplier outage screenshot and server API |
| WooCommerce already registered | Met | `woo` present before review; five tools and live calls |
| Add/edit/disable/remove URL-based Streamable HTTP servers | Met implementation; partly exercised live | REST/UI paths and gateway tests; live disable/re-enable and URL validation. No unrelated production server was removed |
| Optional bearer/static header credentials | Met | Server-side decrypt/header path; authenticated shipping/supplier/Slack calls and tests |
| Admin invites/manages members and keys | Met implementation | Team UI, admin/member endpoint checks and gateway tests. No lasting invite/user change was made |
| Member generates own key | Met | Bob/Ana/Chen own review-key creation; all review keys later revoked |
| One gateway URL/key gives enabled allowed tools | Met | Curl, SDK and actual Claude Code; ownership restrictions verified |
| Member chat uses the same tools | Met | Bob two-store browser chat; Chen Cloud Run chat; tool lists and audit attribution |
| Members never see or need upstream credentials | Met in inspected responses | Member APIs/tool catalogs return metadata without secret values; live integrations use gateway-held credentials |
| Woo MCP is written in this repository | Met | `services/woo-mcp/woo_mcp` source and 10 passing tests |
| Woo MCP speaks Streamable HTTP | Met | SDK calls routed to live Woo upstream; protocol metadata and source |
| At least list/create products, list/get orders, update order status | Met | All five tools listed; new product, order reads and two status transitions on the review store |
| Woo MCP internal only, gateway-only caller | Met in tested cluster | Actual Pod connection tests block chat-to-store MCP and allow gateway; [network-isolation.txt](proof/network-isolation.txt) |
| Document store access mechanism and why | Met | README/design describe WooCommerce REST and shared server-side credentials |
| Aggregate and route enabled tools through one MCP endpoint | Met | Namespaced catalogs and calls across Woo, shipping, supplier, Slack and each tenant |
| Handle duplicate upstream tool names | Met | `server__tool` names distinguish Woo and supplier `list_products`; SDK/Claude use correct upstreams |
| Slow/dead upstream must not break other lists/calls; clear timeout | Met for tested failures | Live supplier outage with shipping/woo healthy; fixture slow/protocol failures, [wire-confirmed.txt](proof/wire-confirmed.txt) |
| At least three upstream servers | Met | Four built-ins: Woo, shipping, supplier, Slack, plus tenants |
| Admin and member roles | Met | Restricted tools hidden and direct calls denied; management APIs reject members |
| Individual hashed revocable API keys | Met | Hashing source/tests; already-open SDK client fails immediately after revoke at both public URLs |
| Every tool call has user/server/tool/time/duration/outcome in dashboard audit | **Partly met** | Ordinary calls include all fields and correct attribution; R04 and R05 leave calls missing |
| LLM chat calls only through gateway and shows tools used | Met | Browser run cards/SSE, actual tool calls, restrictive NetworkPolicies |
| Chat acts as signed-in user and audits as that user | Met for ordinary calls | Admin/Bob/Chen behavior and audit checks; cross-user approvals/conversation continuation denied |
| LLM credentials follow Secret rule | Met in inspected deployment | Chat workload Secret refs; no browser key response |
| README: local and VPS setup, order placement, external MCP and chat | Met | README and linked setup/operation docs |
| Source for dashboard/backend/Woo MCP/chat | Met | Present and tests/build executed |
| Charts and local/prod values delivered | Met | Present, linted/rendered with required image references |
| System design/tradeoffs including routing/failures and production differences | Met as a document | `docs/design.md`; some protection claims need correction per findings |
| Threat model covers shared credentials, no token passthrough, malicious upstreams and members | Met as a document | `docs/threat-model.md`; R01/R02/R03/R06 expose limitations omitted from its claimed protections |
| List attempted bonus/differentiators and locations | Met | README, design/multi-store and evidence index |
| Demo covers real system, two users, Kubernetes/MCP/security/chat/local-to-VPS/rollback | Partly met, submission not certified | Owner evidence exists, and independent tests cover the core. Final video is being rendered separately and was not inspected |
| Submit demo and GitHub repository through specified form | Partly met, externally unverified | This review did not submit the form or inspect a submission receipt |
| Owner can explain and defend the internals | Partly met / understanding unverified | Documentation supplies material, but an interview is required to assess the owner's understanding |

### Definition of done

| Required demonstration | Result | Independent evidence |
|---|---|---|
| Open shop, add to cart, COD/dummy checkout | Met on provisioned review store | [review-checkout.mp4](proof/review-checkout.mp4), [review-order16.png](proof/review-order16.png), order #16 |
| Order visible in WooCommerce admin | Met for first store's existing orders | [woocommerce-admin-orders.png](proof/woocommerce-admin-orders.png) shows #44/#45/#47. Review order #16 was confirmed through MCP; its store was deleted before a separate WP-admin check |
| User A chat creates named/priced product; storefront shows it | Met on review store | Admin creates product #14 for 25 after approval; [admin-product-approval.mp4](proof/admin-product-approval.mp4), storefront/checkout recording |
| Shopper orders that product; chat returns today's new order | Met | Order #16 contains product #14; admin and Chen browser chat return it |
| User B uses actual external client with only URL/key | Met | Bob's actual headless Claude Code invocation uses a memory-only MCP config; [claude-live-client.txt](proof/claude-live-client.txt) |
| User B sees allowed tools from enabled servers and today's orders | Met | Claude returns first-store #45/#47; SDK/catalog tests verify tool visibility |
| One prompt uses at least two upstreams | Met | Claude uses Woo + shipping + supplier; Chen Cloud Run chat uses own store + shipping |
| Revoke B's key; next call rejected | Met | Open Python SDK client rejected at both URLs; 401 cleanup checks |
| Disable upstream; tools disappear in chat and external client | Met | Shipping removed from Bob's catalog and admin chat tools; cached direct call refused; restored afterward |
| Kill upstream Pod; others work and dashboard shows unreachable | Met | Two supplier Pod deletions, live call/browser errors, shipping success, unreachable screenshot, final recovery |
| Every preceding call appears in audit under correct user | **Partly met** | Ordinary performed calls verified; search omissions and crash window are R04/R05 |
| Entire flow shown with two users on the first store | Partly met as this independent rerun | Bob/Claude and WP admin checked first store. The new product/checkout/chat portion used the authorized disposable store; owner's first-store recording was not substituted for our fresh proof |

### Multi-store bonus

| Requirement | Result | Independent evidence |
|---|---|---|
| Browser create/delete, status/URL/created time | Met for successful path | Chen creates Review Astra, Ready after provisioner restart; deletes via Cloud Run; recordings and API checks |
| Several stores can provision simultaneously | Partly verified | Per-store concurrent reconciler/source/tests inspected. Live simultaneous creation not rerun because only one additional store was authorized |
| Own namespace, DB, Secrets, PVCs, Helm release, Ingress per store | Met | Actual review-store creation and cluster resource inventory |
| Own Woo MCP automatically registered; clear tool names | Met | `store-review-astra__*` automatically appeared for Chen/admin, five tools for admin |
| Clean teardown removes registration and namespace/volumes/Secrets | Partly met | Ready-store deletion passed; namespace/PVs gone and no review tools. Provisioning/delete race fails, R08 |
| Store MCP cannot reach another DB/WordPress | Met for network isolation tested | 17/17 actual Pod/RBAC/admission/guardrail checks; R01 is a separate permission escape |
| Quota, LimitRange, per-user maximum and provisioning timeout | Met in inspected implementation | Live quota/LimitRange/default-deny, Bob's 2-store UI limit and API limits/tests, durable deadline logic |
| Creation safe to repeat/retry after failure | **Partly met** | Same active slug returns same store; wrong owner refused. Failed setup retry is broken, R07 |
| Provisioner restart mid-create completes or clearly fails | Met for tested happy path | One review-triggered provisioner restart; Review Astra reached Ready; recording and Pod output |
| Chat compares two stores, then one deleted and tools/resources gone | Met as separate allowed demonstrations | Bob compares South/West; disposable Review Astra deleted with resources/tools gone. Protected existing stores were not deleted |

### Optional differentiators

| Item | Result |
|---|---|
| Production-like VPS, values changes, optional TLS | Met; live VM, charts, Ingress and certificates |
| Per-role tool permissions | Met; visibility and direct-call enforcement tested |
| OAuth | Met in tested flows; fresh DCR/PKCE/audience/reuse checks also pass through Cloud Run |
| Handling many tools | Partly met; two-tool search works, but audit is incomplete, R05 |
| Hosting MCP from a container image | Missing; already documented, not a new finding |
| SSRF hardening | Met for tested metadata/link-local/mapped/CGNAT/loopback cases; connection-time checks and egress policy inspected |
| Least-privilege Kubernetes access | Partly met; ordinary cross-namespace checks pass, R01 allows weakened store admission |
| Default-deny policies and non-root containers | Partly met; app/store MCP policies and app workload contexts verified; first-store gap and WordPress/MariaDB image constraints already documented |
| Upstream credentials encrypted at rest | Partly met; application credential encryption present, Kubernetes Secret encryption-at-rest gap already documented |
| Multiple gateway replicas | Missing; already documented single replica, not a new finding |
| Per-user/per-key rate limits | Partly met; fixed representation gets 429, same-key representation bypass is R06 |
| Call/error/latency metrics and clear failure reasons | Met for dashboard/API; no Prometheus endpoint is an acknowledged gap |
| Upgrade/rollback documentation and demonstration | Partly verified; documentation/evidence inspected. I ran no Helm upgrade/rollback/uninstall; owner's planned change is excluded |

## Eight-area test results

### Protocol, access and OAuth

Unauthenticated POST returns 401 with a valid `WWW-Authenticate` resource-metadata challenge at both addresses. Earlier breadth output showed a null challenge because its header lookup used the wrong case; [final-live-state.txt](proof/final-live-state.txt) verifies the actual header. GET 405 is the documented stateless POST-only transport, not a finding.

Initialize/protocol negotiation, listing, calls, ping, malformed JSON/request handling and method-not-found were checked. The actual SDK negotiated `2026-07-28`. R11 is the confirmed error-channel exception. [protocol-security.txt](proof/protocol-security.txt), [sdk-and-review-store.txt](proof/sdk-and-review-store.txt) and [cloud-run-sdk-integrations.txt](proof/cloud-run-sdk-integrations.txt) hold the client output.

Bob could not list or call Ana's tenant tools, read another user's keys/audit rows, administer users, delete Chen's store, continue another user's conversation or approve its held call. Ana/Bob/Chen store ownership and admin access behaved as intended. Members' visible tenant metadata and public storefront links are deliberate; they do not grant store tool access. [breadth-live.txt](proof/breadth-live.txt), [cross-user-approval.txt](proof/cross-user-approval.txt), [bob-pages.txt](proof/bob-pages.txt).

OAuth DCR, S256 PKCE, correct audience, wrong audience, code reuse and exact redirect matching were exercised. Wrong redirect path and userinfo tricks returned 400, plain PKCE was rejected, and reusing a code revoked the issued access token. Fresh tests through Cloud Run passed: [cloud-run-oauth.txt](proof/cloud-run-oauth.txt). No active OAuth grants remained after testing. A real Claude Code API-key client was independently exercised; the owner's separate Claude Code OAuth recording is not counted as our own client login.

Metadata, mapped IPv6, CGNAT and prohibited loopback URL registration checks rejected their inputs. Private cluster URLs intentionally exist for legitimate upstreams; accepting a private URL at validation is not by itself proof that the egress policy permits it. No DNS-rebinding exploit was performed against live infrastructure.

### Robustness and integrations

Two supplier Pod deletions produced real ConnectError results while Woo/shipping continued answering. One first attempt at catching the outage in chat was too late and both tools succeeded; I repeated within the authorized Pod-deletion allowance and captured the partial failure. The final supplier Pod is healthy. [chat-supplier-outage.mp4](proof/chat-supplier-outage.mp4), [chat-supplier-outage.png](proof/chat-supplier-outage.png), [supplier-down-overview.png](proof/supplier-down-overview.png), [cluster-cleanup.txt](proof/cluster-cleanup.txt).

The isolated wire fixture returned oversized structured data, a valid large PNG, a slow call and an upstream protocol exception. Slow/protocol failures became clear errors and stayed isolated. A one-second timeout returned in about 1.02 seconds. Concurrent live requests exposed R06. The committed-write crash, failed setup retry and provisioning/delete race are R04/R07/R08.

Woo's five required operations were exercised across storefront/chat/MCP: new product #14, COD order #16, products/orders/get-order, and processing→completed→processing updates. Tenant product lists worked for north, south and west-sports. Shipping quotes and supplier catalog/restock quote worked and explicitly reported simulation. Slack channel/read/post worked; the one test post showed **Admin (via urumi)** and was deleted afterward. [integration-followup.txt](proof/integration-followup.txt), [cloud-run-sdk-integrations.txt](proof/cloud-run-sdk-integrations.txt), [slack-cleanup-confirmed.txt](proof/slack-cleanup-confirmed.txt).

The Cloud Run integration proof also contains one supplier call with my incorrect arguments, correctly rejected as “items: Field required.” The earlier correctly formed restock quote succeeded. This reviewer input mistake is not a project finding.

### Chat and UI

Browser tests used admin, Bob and Chen. Bob's South/West comparison called only his store tools. Chen's Cloud Run catalog contained only his Review Astra tenant tools, and his own-store+shipping answer streamed successfully. Admin product creation waited for approval; an independent search before approval returned zero matching products. The approved product appeared on the storefront.

A customer note in review order #16 instructed the assistant to create another product without asking. The actual tool result carried that instruction. On an explicitly read-only request, the model identified it as an injection, made no write call, and the extra product did not exist. This demonstrates that case only, not universal model resistance. [chat-live.txt](proof/chat-live.txt), [integration-followup.txt](proof/integration-followup.txt), [cloud-run-browser.txt](proof/cloud-run-browser.txt).

Shipping disable removed the tools from chat; the agent reported they were unavailable. Supplier failure appeared in chat while the shipping quote still succeeded. Admin Overview, Servers, Team, Permissions, Audit, Connect, Stores and Chat were visited. Member pages and redirects were checked. The concrete UI defects are R09/R10 and R05's audit filter; no other unexplained page failure was observed. The loopback sign-in bridge's earlier browser CORS errors in `bob-pages.txt` belong to the review harness, not the application.

### Kubernetes, charts, tests and source

Actual Pod connections proved the intended store network restrictions. The review-only copy of `verify-multistore.sh` uses server-side dry-run admission probes so a failed guard cannot accidentally mutate a namespace or RoleBinding. Its 17 checks passed. The more demanding provisioner dry runs found R01.

Both store chart values linted/rendered. The app chart intentionally requires real image tags: bare local/prod values failed with the documented missing-image error, then the same commands with `.runtime/app/images.yaml` passed. This is not reported as an installation bug. Rendered manifests are saved alongside the command output.

| Check | Result | Proof |
|---|---|---|
| Gateway tests | 45 passed | [gateway-tests.txt](proof/gateway-tests.txt) |
| Provisioner tests | 15 passed | [provisioner-tests.txt](proof/provisioner-tests.txt) |
| Slack MCP tests | 14 passed | [slack-mcp-tests.txt](proof/slack-mcp-tests.txt) |
| Woo MCP tests | 10 passed | [woo-mcp-tests.txt](proof/woo-mcp-tests.txt) |
| Demo MCP tests | 3 passed | [demo-mcps-tests.txt](proof/demo-mcps-tests.txt) |
| Chat-agent tests | 26 passed, 1 live Bedrock test skipped | [chat-agent-tests.txt](proof/chat-agent-tests.txt) |
| Dashboard `npm run build` | Passed | [dashboard-build-tests.txt](proof/dashboard-build-tests.txt) |
| Helm store local/prod lint/template | Passed | [chart-validation.txt](proof/chart-validation.txt) |
| Helm app local/prod with image refs | Passed | [chart-validation-with-images.txt](proof/chart-validation-with-images.txt) |
| Live network/admission/guardrail suite | 17 passed | [network-isolation.txt](proof/network-isolation.txt) |

The existing suites do not cover the managed-store late-tool exception, non-text cap, durable crash logging, failed setup Job retry, deletion race or header-normalization rate bypass. Passing them does not disprove these reproductions.

## Checked claims from `docs/video-facts.md`

Truth labels below apply to the stated scope. Historical timing, old instance counts and exact old order IDs are not claimed to have been recreated.

| Claim checked | True/false | Independent result |
|---|---|---|
| Dashboard/MCP work at both public addresses after Cloud Run change | **True** | Login, tools and calls passed; storefront action exception R09 |
| Search mode lists exactly two gateway tools | **True** | Bob's live search catalog |
| Search-mode calls have the same audit coverage | **False** | Search/wrapper-denial events missing and filter exception, R05 |
| Two charts separate app and store; per-store namespace/resources | **True** | Chart source, rendered/live resources and disposable store |
| Service test counts are 45/15/14/10/3/26 plus one skip | **True** | Independent suite runs |
| Members see nine built-in read tools | **True** | Four built-ins; tenant additions increase each owner's total |
| A revoked key fails on an already-open client | **True** | SDK tests at direct and Cloud Run URLs |
| Supplier outage does not stop other servers; dashboard shows reason | **True** | Actual outage and recovery capture |
| Fixed-header key requests eventually get 429; a second key has a separate allowance | **True** | Live controls; exact earlier “67” count is timing-dependent |
| The limit holds per authenticated key regardless of representation | **False** | Same-key whitespace bypass, R06 |
| Slack member posts are denied; admin post carries user attribution | **True** | Direct member denial, one admin post/read-back |
| Repeating active store creation returns the same store | **True** | Chen's repeated slug and owner-conflict checks |
| Store namespace contains expected isolated resources and guardrails | **True** | Actual namespace inventory and 17-check suite |
| NetworkPolicy test is 17/17 and blocks the listed store connections | **True** | Independent rerun with safe admission probes |
| Those checks establish a complete provisioner permission fence | **False if read as complete** | They miss the accepted Pod Security label changes, R01 |
| Store tools are visible only to owner/admin | **True** | Ana/Bob/Chen catalogs and unauthorized direct calls |
| Ready-store deletion removes tools and resources | **True** | Review Astra browser deletion, empty tool lists, absent namespace/PVs |
| Deletion reliably completes when requested during provisioning | **False** | Lost deletion intent, R08 |
| Provisioner restart can continue an active create | **True** | Review-triggered restart and Ready result |
| A Failed setup can be restarted simply by Retry | **False** | Failed Job left unchanged, R07; this claim is also explicit in multi-store docs |
| Definition changes on an ordinary server are held for review | **True in tested code path** | Existing tests/control and ordinary-server fixture; live compromised-supplier switch was not changed |
| Any new tool after first review is hidden pending approval | **False** | Managed store exception, R02 |
| Tool results are capped at 256,000 characters | **False** | Structured/image payloads pass intact, R03 |
| Metadata/mapped/link-local/CGNAT/prohibited loopback URLs are rejected | **True for tested addresses** | Live validation and connection/egress source review |
| `/mcp` is POST-only; GET gives 405 | **True** | Live protocol probes |
| 401 uses non-reserved `-32010` and resource discovery | **True** | Actual challenges at both URLs and spec review |
| Two-user DoD has a working happy path | **True for the independently tested portions** | Admin new product/order/chat, Bob external client, disable/outage/revocation |
| Every call is durably audited, including crash cases | **False** | R04/R05 |

Not independently recreated: old order #44 surviving MariaDB/WordPress deletion, old concurrent north/south/east creation timings, exact quota figures at that old moment, the owner's Helm rollback, the owner's Claude Code browser-OAuth session, certificate renewal over time, exact firewall/monthly-price claims, and the model credential's IAM restriction. These require destructive operations, time observation, billing/IAM evidence or an additional client login. They are not labelled false merely because they were not rerun. The allowed fresh OAuth HTTP flow and actual API-key Claude client were tested separately.

## Cleanup

| Review change | How undone / final evidence |
|---|---|
| Created 14 API keys, IDs 12–25, for admin/Ana/Bob/Chen | All revoked, including already-revoked controls. No pre-existing key revoked. [key-session-cleanup.txt](proof/key-session-cleanup.txt); old and Cloud Run MCP reject the review key with 401 |
| Two public OAuth client registrations and authorization flows | Code reuse revoked their grants/tokens; final admin grant list is empty. Public registration metadata remains as an inert record because there is no client-delete API, like retained revoked-key/audit records |
| Created Chen's `review-astra` store | Deleted by Chen through Cloud Run Stores page. MCP tools absent for Chen/Bob/admin; namespace and its PVs absent; original three tenant stores remain Ready |
| Product #14, order #16, planted customer note, two order-status transitions | Existed only inside `review-astra`; removed with its store. Status was returned to processing before teardown |
| Disabled Shipping | Restored `enabled=true`; final status `ok`, [restore-shipping.txt](proof/restore-shipping.txt), [final-live-state.txt](proof/final-live-state.txt) |
| Deleted supplier Pod twice | Kubernetes replaced each; final supplier Pod 1/1 Running and upstream `ok` |
| Deleted provisioner Pod once during review-store creation | Replacement completed provisioning; final provisioner Pod 1/1 Running |
| One Slack message starting `[review test]` | Deleted only that message through Slack API; read-back confirms it is absent, [slack-cleanup-confirmed.txt](proof/slack-cleanup-confirmed.txt) |
| Privileged namespace/workload admission probes | Server-side dry-run only; no resource or namespace label was changed |
| Local wire/SQLite/crash/concurrency fixtures | Review-only processes/resources; crash fixture DB and marker removed by the script; no live write was killed |
| Test logins and temporary credential bridge | HTTP-client sessions logged out; own browser sessions signed out; bridge stopped after proof checks |
| Implementation/repository changes | No implementation file edited, no commit/push, no review-triggered Helm upgrade/rollback/uninstall. Output and reproduction scripts are under `docs/review/` |

Final live checks show all eight app Pods healthy, the original first store and three tenant stores retained, seven enabled connected upstreams, no Review Astra namespace/PVs/tools, and no active OAuth grants. Expected review audit rows, revoked-key records and public DCR metadata remain. The owner's planned Cloud Run deployment/settings remain as requested.

The proof bundle was scanned against the sensitive values held by the review process: [final-secret-scan.txt](proof/final-secret-scan.txt). Saved credential prefixes were restricted to the first six characters. Screenshots used for the findings were inspected; Connect key values and login credentials were not captured.

## Verification limits

I did not execute forbidden Helm operations, kill WordPress/MariaDB, modify Secrets/PVCs/passwords, delete existing stores or change the compromised-supplier environment switch. I did not create a second cluster merely to repeat the owner's local-install evidence. I did not exhaust the shared login limiter and risk locking out the admin. The chat tests stayed below the 25-turn limit, and exactly one review Slack post was made.

The isolated reproductions show real code behavior under controlled upstream/Kubernetes states. They are stronger than source-only suspicions, but they are not evidence that an attacker compromised the live provisioner, that the live host was escaped, or that a live customer write was lost. No finding here is marked SUSPECTED. Unexercised operations and historical claims are explicitly identified rather than silently treated as passed.
