#!/usr/bin/env bash
# Run the whole MCP side locally, without Kubernetes and without a real store:
#   fake WooCommerce API -> woo-mcp, shipping, supplier -> gateway (SQLite)
#
#   bash scripts/dev-stack.sh start | stop | status | logs
#
# Ports: gateway 8080, fake store 18911, woo-mcp 18912, shipping 18921, supplier 18922, slack 18931.
# The Slack server starts only if ~/.urumi/slack.env exists (see infra/slack/README.md).
# Dashboard login: admin@urumi.test / admin-password-1
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/.runtime/dev"
mkdir -p "$RUN"

start_one() {  # name dir env... -- command...
  local name=$1 dir=$2; shift 2
  local envs=()
  while [[ $1 != "--" ]]; do envs+=("$1"); shift; done; shift
  if [[ -f "$RUN/$name.pid" ]] && kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null; then
    echo "$name already running"; return
  fi
  # exec: no shell lingers holding the caller's output open. setsid gives the server
  # its own process group, so `stop` can end uv and its Python child together.
  (
    cd "$ROOT/services/$dir"
    exec env "${envs[@]}" PYTHONUNBUFFERED=1 PIDFILE="$RUN/$name.pid" \
      setsid bash -c 'echo $$ >"$PIDFILE"; exec uv run "$@"' _ "$@"
  ) </dev/null >"$RUN/$name.log" 2>&1 &
  sleep 0.2
  echo "started $name (log: .runtime/dev/$name.log)"
}

wait_http() {
  for _ in $(seq 1 60); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 0.5; done
  echo "timed out waiting for $1" >&2; return 1
}

case "${1:-}" in
  start)
    start_one fake-store woo-mcp X=1 -- uvicorn tests.dev_store:app --port 18911 --log-level warning
    start_one woo-mcp woo-mcp WOO_STORE_URL=http://shop.urumi.test:18080 WOO_API_URL=http://127.0.0.1:18911 \
      WOO_CONSUMER_KEY=ck_test WOO_CONSUMER_SECRET=cs_test -- uvicorn woo_mcp.server:app --port 18912 --log-level warning
    start_one shipping demo-mcps SHIPPING_API_TOKEN=dev-shipping-token -- uvicorn demo_mcps.shipping:app --port 18921 --log-level warning
    start_one supplier demo-mcps SUPPLIER_API_KEY=dev-supplier-key -- uvicorn demo_mcps.supplier:app --port 18922 --log-level warning
    SLACK_ENV="$HOME/.urumi/slack.env"
    if [[ -f $SLACK_ENV ]]; then
      slack_var() { grep -E "^$1=" "$SLACK_ENV" | head -1 | cut -d= -f2-; }  # only these two, not the whole file
      start_one slack-mcp slack-mcp SLACK_BOT_TOKEN="$(slack_var SLACK_BOT_TOKEN)" SLACK_CHANNEL_ID="$(slack_var SLACK_CHANNEL_ID)" \
        SLACK_MCP_TOKEN=dev-slack-token -- uvicorn slack_mcp.server:app --port 18931 --log-level warning
    fi
    start_one gateway gateway DATABASE_URL="sqlite+aiosqlite:///$RUN/gateway.db" GATEWAY_SECRET_KEY=dev-only-secret-key-0123 \
      BOOTSTRAP_ADMIN_EMAIL=admin@urumi.test BOOTSTRAP_ADMIN_PASSWORD=admin-password-1 ALLOW_LOOPBACK_UPSTREAMS=true \
      PUBLIC_URL=http://localhost:8080 REFRESH_INTERVAL_SECONDS=15 -- uvicorn gateway.main:app --port 8080
    wait_http http://127.0.0.1:8080/readyz
    for p in 18912 18921 18922; do wait_http "http://127.0.0.1:$p/healthz"; done
    if [[ -f $SLACK_ENV ]]; then wait_http "http://127.0.0.1:18931/healthz"; fi
    (cd "$ROOT/services/gateway" && uv run python "$ROOT/scripts/dev_seed.py")
    ;;
  stop)
    for f in "$RUN"/*.pid; do [[ -f $f ]] || continue; kill -- "-$(cat "$f")" 2>/dev/null || true; rm -f "$f"; done
    echo stopped
    ;;
  status)
    for f in "$RUN"/*.pid; do [[ -f $f ]] || continue; n=$(basename "$f" .pid)
      if kill -0 "$(cat "$f")" 2>/dev/null; then echo "$n: running"; else echo "$n: dead"; fi; done
    ;;
  logs) tail -n 30 "$RUN"/*.log ;;
  *) echo "usage: $0 start|stop|status|logs" >&2; exit 2 ;;
esac
