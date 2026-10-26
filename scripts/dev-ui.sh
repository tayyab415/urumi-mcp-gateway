#!/usr/bin/env bash
# The dashboard side of local development: the chat service and the Vite dev server.
#   bash scripts/dev-ui.sh start | stop | status | logs
# Needs the MCP side first (scripts/dev-stack.sh start) and AWS credentials for Bedrock
# in the environment or ~/.aws (the chat service signs short-lived Bedrock keys from them).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/.runtime/dev"
mkdir -p "$RUN"

running() { [[ -f "$RUN/$1.pid" ]] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

case "${1:-}" in
  start)
    if running chat-agent; then echo "chat-agent already running"; else
      (cd "$ROOT/services/chat-agent" && GATEWAY_MCP_URL=http://127.0.0.1:8080/mcp AWS_REGION="${AWS_REGION:-us-east-1}" PYTHONUNBUFFERED=1 \
        setsid uv run uvicorn chat_agent.server:app --host 127.0.0.1 --port 8200 >"$RUN/chat-agent.log" 2>&1 & echo $! >"$RUN/chat-agent.pid")
      echo "started chat-agent on :8200 (log: .runtime/dev/chat-agent.log)"
    fi
    if running dashboard; then echo "dashboard already running"; else
      [[ -d "$ROOT/dashboard/node_modules" ]] || (cd "$ROOT/dashboard" && npm install --no-audit --no-fund)
      (cd "$ROOT/dashboard" && setsid npm run dev >"$RUN/dashboard.log" 2>&1 & echo $! >"$RUN/dashboard.pid")
      echo "started dashboard on http://127.0.0.1:5173 (log: .runtime/dev/dashboard.log)"
    fi
    for _ in $(seq 1 40); do curl -fsS http://127.0.0.1:8200/healthz >/dev/null 2>&1 && break; sleep 0.5; done
    curl -fsS http://127.0.0.1:8200/api/chat/meta && echo
    ;;
  stop)
    for n in chat-agent dashboard; do
      [[ -f "$RUN/$n.pid" ]] || continue
      kill -- "-$(cat "$RUN/$n.pid")" 2>/dev/null || kill "$(cat "$RUN/$n.pid")" 2>/dev/null || true
      rm -f "$RUN/$n.pid"
    done
    echo stopped
    ;;
  status)
    for n in chat-agent dashboard; do if running "$n"; then echo "$n: running"; else echo "$n: stopped"; fi; done
    ;;
  logs) tail -n 30 "$RUN/chat-agent.log" "$RUN/dashboard.log" ;;
  *) echo "usage: $0 start|stop|status|logs" >&2; exit 2 ;;
esac
