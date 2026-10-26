# Urumi dashboard

The React dashboard for the team MCP gateway: servers, team and keys, audit log, permissions, chat with a human-in-the-loop approval gate, and a page-aware assistant beside every page. The look and motion follow the Arc Library dark theme and springs (see `src/arc/`).

## Run it locally

```bash
bash scripts/dev-stack.sh start          # fake store, 3 MCP servers, gateway on :8080
bash scripts/dev-ui.sh start             # chat service on :8200 (needs AWS credentials for Bedrock) and Vite on :5173
```

Open <http://127.0.0.1:5173>. Sign in as `admin@urumi.test` / `admin-password-1`. Vite proxies `/api/chat` to the chat service and the rest of `/api` and `/mcp` to the gateway, the same split the cluster Ingress makes.

```bash
npm run typecheck   # strict TypeScript
npm run build       # static bundle in dist/
```

## Layout

```text
src/
  styles/        Arc foundation.css (MIT), our tokens, global rules (shimmer, tint sweep)
  lib/           api client, tool-name → words, motion presets, clock, formatting, scripted runs
  arc/           Arc Library components copied unchanged (text-shimmer, text-morph, confirm-morph, copy-button, ...)
  components/
    ui/          buttons, cards, fields, switch, tabs, drawer, toasts, status orb, table, ...
    shell/       left rail, page header
    run-card/    the agent run card (pill → card) and the corner pill for background runs
    task-card/   the chat task card (steps, review block, folded result)
    chat/        composer, thread, docked assistant
  state/         auth, chat (SSE stream → turns and steps), generic resource hook
  pages/         Login, Overview, Servers, Chat, Team, Connect, Audit, Permissions, Stores, Lab
```

## How a chat turn becomes a card

1. `POST /api/chat` with the user's chat token. The reply is a stream of events.
2. `tool_call` adds a live row. `tool_result` settles it (past tense, duration, green check).
3. For a tool that changes data the service pauses before calling the gateway and sends `approval_required`. The card shows the review row and Approve / Deny. The timer stops.
4. Approve or Deny goes to `POST /api/chat/approve`. The service resumes (or refuses the call) and sends `approval_decided`, then the `tool_result`.
5. `done` folds the steps into one line and shows what changed. "Open run" opens the audit log filtered to this conversation (the chat service tags its gateway calls with `X-Urumi-Chat-Id`).

The hold is real: the gateway is not called until Approve arrives (`services/chat-agent/chat_agent/approvals.py`, tested).

## Where the design rules live

- Tokens: `src/styles/tokens.css` adds only what Arc lacks (`--text-live`, `--accent-soft`, the orb fill, card radii).
- Motion: `src/lib/motion.ts` maps Arc's `smooth` / `morph` / `snappy` springs to `settle` / `morph` / `snap` and holds the real-time numbers (900 ms orb turn, 570 ms shimmer sweep every 2.4 s).
- Words: `src/lib/labels.ts` turns `woo__update_order_status {order_id: 1043}` into "Preparing a change to order #1043", then "Updated order #1043".
- The Design lab (`/lab`) shows every card state and replays the reference clip's sequence with real timers, so motion can be judged against the frames in `docs/design-reference/`.

## Licensing

`src/arc/` is MIT, from Arc Library by Elia Kuratli (see `src/arc/LICENSE` and `SOURCE.md`). The run card and task card are our own builds on top of the free parts; the reference clips show Arc Pro components that were not copied.
