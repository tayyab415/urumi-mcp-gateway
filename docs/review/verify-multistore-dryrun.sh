#!/usr/bin/env bash
# Checks the multi-store guarantees on the VM's cluster and prints PASS/FAIL per check.
#   bash scripts/verify-multistore.sh <store-a> <store-b>      e.g. north south
# Needs both stores Ready. Read-only except for one object the admission policy must refuse.
set -uo pipefail
a="${1:-north}" b="${2:-south}"
vm() { bash "/home/tayyabkhan/urumi-learning/scripts/gcp-vm.sh" "$@" 2>/dev/null; }
pass=0 fail=0
check() { if [[ "$2" == "$3" ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1 (expected $3, got $2)"; fail=$((fail+1)); fi; }

echo "== 1. Network isolation: connections from store '$a' MCP server (NetworkPolicy)"
probe=$(cat <<PY
import socket
targets = [("own WordPress", "$a-wordpress.store-$a.svc.cluster.local", 80),
           ("own database", "$a-mariadb.store-$a.svc.cluster.local", 3306),
           ("other store's WordPress", "$b-wordpress.store-$b.svc.cluster.local", 80),
           ("other store's database", "$b-mariadb.store-$b.svc.cluster.local", 3306),
           ("first store's WordPress", "urumi-wordpress.urumi-store.svc.cluster.local", 80),
           ("gateway", "urumi-gateway.urumi-app.svc.cluster.local", 8080),
           ("cloud metadata", "169.254.169.254", 80)]
for label, host, port in targets:
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, port)); print(f"{label}|open")
    except Exception as e:
        print(f"{label}|blocked")
    finally:
        s.close()
PY
)
out=$(vm kube exec -n "store-$a" "deploy/$a-woo-mcp" -- python -c "$probe")
want=("own WordPress|open" "own database|blocked" "other store's WordPress|blocked" "other store's database|blocked"
      "first store's WordPress|blocked" "gateway|blocked" "cloud metadata|blocked")
for w in "${want[@]}"; do
  label="${w%%|*}"; got=$(grep -F "$label|" <<<"$out" | head -1 | cut -d'|' -f2)
  check "$a woo-mcp -> $label: ${w##*|}" "${got:-missing}" "${w##*|}"
done

echo "== 2. Only the gateway may call a store's MCP server"
out=$(vm kube exec -n urumi-app deploy/urumi-chat -- python -c "
import socket; s=socket.socket(); s.settimeout(3)
try: s.connect(('$a-woo-mcp.store-$a.svc.cluster.local', 8000)); print('open')
except Exception: print('blocked')")
check "chat Pod -> store $a woo-mcp: blocked" "$out" "blocked"
out=$(vm kube exec -n urumi-app deploy/urumi-gateway -- python -c "
import socket; s=socket.socket(); s.settimeout(3)
try: s.connect(('$a-woo-mcp.store-$a.svc.cluster.local', 8000)); print('open')
except Exception: print('blocked')")
check "gateway Pod -> store $a woo-mcp: open" "$out" "open"

echo "== 3. The provisioner's permissions (RBAC + admission policy)"
sa=system:serviceaccount:urumi-app:urumi-provisioner
check "provisioner may read Secrets in store-$a" "$(vm kube auth can-i get secrets -n "store-$a" --as=$sa)" "yes"
check "provisioner may NOT read Secrets in urumi-app" "$(vm kube auth can-i get secrets -n urumi-app --as=$sa)" "no"
check "provisioner may NOT touch kube-system" "$(vm kube auth can-i create deployments -n kube-system --as=$sa)" "no"
out=$(bash "/home/tayyabkhan/urumi-learning/scripts/gcp-vm.sh" kube create rolebinding escalate-test --dry-run=server -n urumi-app --clusterrole=urumi-store-tenant --serviceaccount=urumi-app:urumi-provisioner --as=$sa 2>&1 | tail -1)
grep -q "store-namespaces-only" <<<"$out" && r=denied || r="allowed: $out"
check "provisioner granting itself the tenant role in urumi-app: denied by the admission policy" "$r" "denied"
out=$(bash "/home/tayyabkhan/urumi-learning/scripts/gcp-vm.sh" kube label namespace urumi-app --dry-run=server urumi.io/store=true --as=$sa 2>&1 | tail -1)
grep -q "store-namespaces-only\|may not turn" <<<"$out" && r=denied || r="allowed: $out"
check "provisioner labelling urumi-app as a store: denied" "$r" "denied"

echo "== 4. Guardrails in store-$a"
check "ResourceQuota present" "$(vm kube get resourcequota -n "store-$a" -o name | wc -l | tr -d ' ')" "1"
check "LimitRange present" "$(vm kube get limitrange -n "store-$a" -o name | wc -l | tr -d ' ')" "1"
check "default-deny NetworkPolicy present" "$(vm kube get networkpolicy -n "store-$a" "$a-default-deny" -o name)" "networkpolicy.networking.k8s.io/$a-default-deny"

echo
echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
