#!/usr/bin/env bash
# Quick check of the live VM before a demo: endpoints, OAuth discovery, each
# person's tools, the stores, every Pod. Read-only. Prints what it sees.
#   bash scripts/smoke.sh                                            through Cloud Run
#   GATEWAY=https://app.34-67-189-86.sslip.io bash scripts/smoke.sh  the VM directly
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
G="${GATEWAY:-https://urumi-1027824348124.us-central1.run.app}"
C="$HOME/.urumi/vm-team-credentials.json"
echo "== Endpoints"
echo "GET  /mcp            -> $(curl -s -o /dev/null -w '%{http_code}' $G/mcp)   (405: POST only)"
echo "POST /mcp, no key    -> $(curl -s -o /dev/null -w '%{http_code}' -X POST $G/mcp -H 'content-type: application/json' -d '{}')   (401)"
echo "401 points to OAuth  -> $(curl -s -D - -o /dev/null -X POST $G/mcp -d '{}' | grep -io 'resource_metadata="[^"]*"')"
echo "OAuth metadata       -> $(curl -s -o /dev/null -w '%{http_code}' $G/.well-known/oauth-authorization-server) / $(curl -s -o /dev/null -w '%{http_code}' $G/.well-known/oauth-protected-resource/mcp)"
echo "Store                -> $(curl -s -o /dev/null -w '%{http_code}' https://shop.34-67-189-86.sslip.io/)"
echo "== Each person's tools"
cd "$root/services/gateway"
for who in ana bob chen dev; do uv run python ../../scripts/mcp_cli.py --gateway $G/mcp --creds "$C" --as $who tools 2>&1 | tail -1 | cut -c1-200; done
echo "search mode (bob)    -> $(uv run python ../../scripts/mcp_cli.py --gateway $G/mcp/search --creds "$C" --as bob tools 2>&1 | tail -1)"
echo "== Stores"
uv run python ../../scripts/store_cli.py --as admin list
echo "== Pods not Running/Completed"
bash "$root/scripts/gcp-vm.sh" kube get pods -A --no-headers 2>/dev/null | awk '$4!="Running" && $4!="Completed"' | sed 's/^/  /'
echo "(none listed above means all good)"
