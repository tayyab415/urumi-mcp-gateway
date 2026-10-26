#!/usr/bin/env bash
# Share the dashboard on a public URL from this VM, with no account.
#   bash scripts/dev-tunnel.sh start | stop | url | status
# Serves the production build with `vite preview` on :4173 (gzip, and the same /api and /mcp
# proxy as dev), then keeps an SSH tunnel to pinggy.io open and reconnects when it drops.
# A free pinggy session lasts 60 minutes, and every reconnect gets a new hostname, so read
# the live one with `url`. (localhost.run was tried first: it throttles to ~16 kB/s.)
#
# The shared build signs in on its own as the dev admin, so the link opens straight on the
# dashboard. Anyone with the link is that admin. Turn it off with: DEMO_LOGIN=off bash scripts/dev-tunnel.sh start
# (or set DEMO_LOGIN=email:password for another account). Signing out shows the normal login page.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/.runtime/dev"; mkdir -p "$RUN"
PORT=4173
alive() { [[ -f "$RUN/$1.pid" ]] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }
port_pid() { ss -ltnp "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true; }

case "${1:-}" in
  start)
    if [[ -z "$(port_pid)" ]]; then
      demo="${DEMO_LOGIN:-admin@urumi.test:admin-password-1}"; [[ "$demo" == off ]] && demo=""
      (cd "$ROOT/dashboard" && VITE_DEMO_LOGIN="$demo" npm run build >"$RUN/preview-build.log" 2>&1)
      (cd "$ROOT/dashboard" && exec setsid npx vite preview --host 127.0.0.1 --port $PORT --strictPort >"$RUN/preview.log" 2>&1 </dev/null) &
      for _ in $(seq 1 40); do curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break; sleep 0.5; done
    fi
    if ! alive tunnel-loop; then
      rm -f "$RUN/tunnel.url"; : >"$RUN/tunnel.log"
      setsid bash -c '
        while true; do
          ssh -p 443 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=15 \
              -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -T -R0:127.0.0.1:'$PORT' a.pinggy.io 2>&1 \
            | while IFS= read -r line; do
                clean=$(printf "%s" "$line" | sed "s/\x1b\[[0-9;]*m//g")
                echo "$clean" >>"'"$RUN"'/tunnel.log"
                u=$(printf "%s" "$clean" | grep -oE "https://[a-z0-9-]+\.run\.pinggy-free\.link" | head -1 || true)
                [[ -n $u ]] && echo "$u" >"'"$RUN"'/tunnel.url"
              done
          rm -f "'"$RUN"'/tunnel.url"; sleep 3
        done' >/dev/null 2>&1 </dev/null &
      echo $! >"$RUN/tunnel-loop.pid"
    fi
    for _ in $(seq 1 40); do [[ -s "$RUN/tunnel.url" ]] && break; sleep 0.5; done
    cat "$RUN/tunnel.url" 2>/dev/null || { echo "no URL yet; try: bash scripts/dev-tunnel.sh url" >&2; exit 1; }
    ;;
  url) cat "$RUN/tunnel.url" 2>/dev/null || { echo "tunnel is reconnecting; retry in a few seconds" >&2; exit 1; } ;;
  stop)
    if [[ -f "$RUN/tunnel-loop.pid" ]]; then
      kill -- "-$(cat "$RUN/tunnel-loop.pid")" 2>/dev/null || kill "$(cat "$RUN/tunnel-loop.pid")" 2>/dev/null || true
      rm -f "$RUN/tunnel-loop.pid"
    fi
    pkill -f '[a]\.pinggy\.io' 2>/dev/null || true
    pid="$(port_pid)"; [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    rm -f "$RUN/tunnel.url" "$RUN/preview.pid"; echo stopped ;;
  status)
    if [[ -n "$(port_pid)" ]]; then echo "preview: running"; else echo "preview: stopped"; fi
    if alive tunnel-loop; then echo "tunnel-loop: running"; else echo "tunnel-loop: stopped"; fi
    cat "$RUN/tunnel.url" 2>/dev/null || echo "url: none" ;;
  *) echo "usage: $0 start|stop|url|status" >&2; exit 2 ;;
esac
