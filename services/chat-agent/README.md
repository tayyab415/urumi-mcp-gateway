# chat-agent

The dashboard chat. The OpenAI Agents SDK runs the tool loop with Kimi K3 on Amazon Bedrock. Every tool call goes through the gateway as the signed-in user.

Chat is just another MCP client of the gateway. It connects to the same `/mcp` endpoint that Claude Code or MCP Inspector use, with the user's credential. So chat gets the same checks for free: identity, disabled servers, permissions, timeouts, and the audit row under that user. Chat never talks to an upstream server and never decides who the user is.

## One turn

```text
browser ──POST /api/chat (Bearer <user credential>)──▶ chat-agent
chat-agent ──MCP tools/list (same Bearer)──▶ gateway            fresh list every turn
Agents SDK ──conversation + tools──▶ Kimi K3 on Bedrock
Kimi ──"call woo__create_product {...}"──▶ Agents SDK
chat-agent ──SSE approval_required──▶ browser        only for a tool that can change data
browser ──POST /api/chat/approve {approve|deny}──▶ chat-agent     the call is held until this
Agents SDK ──MCP tools/call (same Bearer)──▶ gateway ──▶ WooCommerce MCP ──▶ store
Agents SDK ──tool result──▶ Kimi ... repeat until Kimi answers without a tool call
chat-agent ──SSE events: tools, tool_call, approval_*, tool_result, text, done──▶ browser
```

The SDK owns the loop (`Runner.run_streamed`). This service owns the edges of it, and that is where the project-specific decisions are:

| File | What it decides |
|---|---|
| [`agent.py`](chat_agent/agent.py) | Instructions, step limit, tracing off, SDK stream → dashboard events, keeping the history valid |
| [`gateway.py`](chat_agent/gateway.py) | User's credential on every request, tools listed once per turn, **no retries**, wording of every failure, the hold before a write |
| [`approvals.py`](chat_agent/approvals.py) | The approval gate: who may answer, how long it waits, what a denial says to the model |
| [`model.py`](chat_agent/model.py) | Kimi K3 through Bedrock's OpenAI-compatible endpoint, Bedrock key handling |
| [`server.py`](chat_agent/server.py) | HTTP + SSE API, conversation ownership |

The model picks tools and arguments. It never sees a credential.

## What the gateway needs to do for chat

1. **Accept the dashboard user's credential on `/mcp`** as `Authorization: Bearer <token>`, and attribute calls to that user. Chat forwards whatever token the dashboard holds. The gateway can accept a dashboard session token alongside API keys, with both checked by one `authenticate(bearer) -> user` function.
2. **Answer a bad or revoked credential with HTTP 401.** Chat detects 401/403 itself and shows "credential not accepted". It does this because the MCP SDK reports any HTTP error as a generic protocol error.
3. **Keep the upstream timeout below 45 s**, chat's per-call limit (`CHAT_TOOL_TIMEOUT_SECONDS`). Then the gateway's clearer timeout error reaches the model first.

## Approval gate

A tool that does not declare `readOnlyHint: true` can change data, so chat does not run it on the model's say-so. The call is held **before** it reaches the gateway, the dashboard is asked, and the gateway only sees the call after the user approves. Read-only tools never wait.

Events in one gated call, in this order:

```text
{"type":"tool_call","id":"<call id>","name":"woo__update_order_status","input":{...}}
{"type":"approval_required","request_id":"<id>","call_id":"<call id>","name":"woo__update_order_status",
 "input":{...},"summary":"Run woo__update_order_status on WooCommerce","server":"woo","read_only":false}
{"type":"approval_decided","request_id":"<id>","decision":"approve" | "deny" | "timeout"}
{"type":"tool_result","id":"<call id>","name":"...","is_error":false,"duration_ms":812,"output":"..."}
```

The dashboard answers with:

```text
POST /api/chat/approve
Authorization: Bearer <the same credential>
{"conversation_id":"...","request_id":"...","decision":"approve"}
```

| Reply | Meaning |
|---|---|
| 200 `{"ok":true}` | The held call now runs (approve) or is refused (deny). |
| 401 | No bearer credential. |
| 404 | Unknown request, or it belongs to another user's conversation. Both look the same on purpose. |
| 409 | Already decided. |
| 400 | Bad body. |

A denial goes back to the model as a tool result, `is_error: true`, "The user denied this call. It did not run." The `tool_result` event then carries `"denied": true`. No answer within `CHAT_APPROVAL_TIMEOUT_SECONDS` counts as a denial too, with `"timed_out": true` and "No decision was made in time. The call did not run." Time spent waiting for the user is not counted in `duration_ms`.

Who may answer: the request is tied to the conversation, and the conversation to the hash of the credential that started it. The same credential has to sign the approval.

`tool_call` is sent by the gateway session itself the moment the call is known, not by the SDK's stream, so it always lands before `approval_required`. (The SDK does emit its `tool_called` item before running the tool, but on a queue chat reads concurrently, so the order was not guaranteed.) The SDK's copy is skipped.

`GET /api/chat/meta` returns `{"model","model_label","approval_timeout_seconds"}` so the dashboard can print the real model name. The `conversation` event carries the same `model` and `model_label`.

## Behaviour worth knowing

| Situation | What happens |
|---|---|
| Admin disables a server | The next message lists tools fresh, so its tools are gone. A stale call to it is refused by the gateway, and the model is told "did not run". |
| The model names a tool it wasn't given (disabled before this turn, hidden by role, or made up) | The call never leaves chat. The model gets "There is no tool named … The call did not run" as the tool result and can explain. Without this the SDK would end the turn with an error. The gateway never sees the call, so the chat log records it instead of the audit log. |
| Key revoked mid-session | The next call gets 401. The model is told "the call did not run". The gateway's 401 carries JSON-RPC code `-32010`; it used to send `-32001`, which the MCP SDK uses for "request timed out", so chat reported the refusal as "may have run". |
| Tool call times out | The model is told "outcome unknown, may have run, don't retry, offer to check". |
| Any tool failure | **Never retried.** The SDK can retry a failed MCP call in a fresh session. That's off (`max_retry_attempts=0`), because a retried `create_product` could create two products. |
| Gateway down or credential bad at turn start | An error event comes back and **no model call is made**, so a stranger can't spend Bedrock money. |
| Browser disconnects mid-turn | The turn finishes in its own task. Any call left without a result gets a "not run" result, so the stored history stays valid. |
| Loop doesn't converge | Stops after `CHAT_MAX_STEPS` model calls (8). |

Conversations live in memory, tied to a hash of the credential that started them. Another user gets "not found". A conversation is stored only after the gateway accepted a turn. A Pod restart loses conversations. The audit log, which is what matters, lives in the gateway.

## Model and credentials

- **Model:** `us.moonshotai.kimi-k3`, Moonshot's Kimi K3 through Bedrock's US cross-region inference profile. Bedrock serves it on Bedrock Runtime's OpenAI-compatible endpoint (`https://bedrock-runtime.<region>.amazonaws.com/openai/v1`) with the Responses API, so the SDK's native `OpenAIResponsesModel` works with only a different base URL.
- **Why Kimi K3.** Checked live on 2026-10-25, one tool-calling request per model and API:

  | Model | Responses API | Chat Completions | Notes |
  |---|---|---|---|
  | Kimi K3 | tool call, ~1 s | tool call | chosen; the real loop answered 5 of 5 runs correctly in 1.3–2.6 s |
  | Kimi K2.5 | refused | tool call | would need the Chat Completions model type |
  | Amazon Nova 2 Lite | refused | refused | tool calls work only on Bedrock's Converse API, which needs a separate adapter |
  | Amazon Nova Premier | end of life | end of life | |
  | GPT-6 Luna (previous) | tool call | refused | |

- **Credential:** a Bedrock API key from the Secret (`AWS_BEARER_TOKEN_BEDROCK`). It belongs to the IAM user `urumi-chat-bedrock`, which has no console login, no access keys and one inline policy: `bedrock:InvokeModel*` on the Kimi K3 inference profile, its foundation model and `project/default` (the OpenAI-compatible endpoint checks that project too), plus `bedrock:CallWithBearerToken`. A call for any other model is refused. The key expires after 30 days. Without `AWS_BEARER_TOKEN_BEDROCK`, `model.py` signs short-lived keys from the AWS credentials the process already has, which is how local development works.
- **Tracing is off.** By default the SDK sends traces (prompts, tool inputs and outputs) to OpenAI's platform. Here that would mean store orders and customer names leaving for a third party.
- A typical turn is 2–3 model calls. In the live test, a two-step turn used about 2,800 input tokens (half of them cached) and 250 output tokens.

### Why the OpenAI Agents SDK, not the Claude Agent SDK

The Claude Agent SDK runs Claude Code in a subprocess, with Bash and file tools built in. In a Pod next to a model credential, those tools would be one prompt-injected tool description away from use. The OpenAI Agents SDK is a plain Python library: an agent loop with an MCP client, and no built-in tools.

An earlier version of this service wrote the loop by hand around Claude Haiku 5.5. It is saved in `.runtime/chat-agent-claude-loop-2026-10-23/`, which is useful when explaining what the SDK does underneath.

## Run it locally

```bash
uv sync
uv run python -m tests.fake_gateway                 # terminal 1: fake gateway on :8100
uv run python -m chat_agent.cli --token dev-user-a  # terminal 2: chat as user A
```

The fake gateway is real MCP over Streamable HTTP. It checks bearer tokens (`dev-user-a`, `dev-user-b`) and serves fake `woo__`, `stock__` and `crm.` tools. It exists so chat can be built before the gateway is ready. Point `--gateway` at the real gateway when it exists.

HTTP API, as the dashboard will call it:

```bash
GATEWAY_MCP_URL=http://127.0.0.1:8100/mcp uv run uvicorn chat_agent.server:app --port 8200
curl -N localhost:8200/api/chat -H 'Authorization: Bearer dev-user-a' \
  -H 'content-type: application/json' -d '{"message":"What orders came in today?"}'
```

Tests:

```bash
uv run pytest                                # agent turn, gateway failures, HTTP API (scripted model)
RUN_LIVE_BEDROCK=1 uv run pytest -m live -s  # real Kimi K3 against the fake gateway
```

The live test runs the brief's chat stories. User A creates "Training Ball" for 25, then asks for today's orders. User B asks one question that needs two servers. The stock server is disabled, and its tools disappear. B's key is revoked, and the next message is refused.

## Settings

| Variable | Default |
|---|---|
| `GATEWAY_MCP_URL` | `http://127.0.0.1:8100/mcp` |
| `CHAT_MODEL` | `us.moonshotai.kimi-k3` |
| `AWS_REGION` | `us-east-1` |
| `AWS_BEARER_TOKEN_BEDROCK` | from a Secret, or unset to sign short-lived keys from AWS credentials |
| `CHAT_EFFORT` | `low` |
| `CHAT_MAX_STEPS` | `8` |
| `CHAT_TOOL_TIMEOUT_SECONDS` | `45` |
| `CHAT_APPROVAL_TIMEOUT_SECONDS` | `300` |
| `CHAT_MAX_MESSAGE_CHARS` | `4000` |
| `CHAT_MAX_TURNS` | `30` |
| `CHAT_CONVERSATION_TTL_SECONDS` | `3600` |

## Kubernetes notes, for when it joins the chart

- One Deployment, port 8000, `GET /healthz` for liveness and readiness. Startup takes a few seconds, more on a busy node, so give it a `startupProbe`.
- Egress NetworkPolicy: the gateway Service, DNS, and HTTPS to Bedrock only. Chat has no reason to reach an upstream MCP server, and the policy proves it can't.
- Ingress: route `/api/chat` (and so `/api/chat/approve`, `/api/chat/meta`) to this Service. Disable proxy buffering so SSE events stream through.
- Conversations are in memory, so one replica, or sticky sessions if scaled.
