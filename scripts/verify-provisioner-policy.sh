#!/usr/bin/env bash
# The provisioner's admission policy on the VM (review finding R01): server-side dry runs as
# the provisioner's ServiceAccount. Nothing changes: every kubectl call carries --dry-run=server.
# Each line prints what the API server did; "ok" means the policy behaved as intended.
#   bash scripts/verify-provisioner-policy.sh            (uses the store namespace store-north)
#   STORE_NS=store-south bash scripts/verify-provisioner-policy.sh
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # the repo root: scripts/gcp-vm.sh lives there
scratch=$(mktemp -d)
SA=system:serviceaccount:urumi-app:urumi-provisioner
kc() { bash scripts/gcp-vm.sh kube "$@"; }
pass=0; fail=0

check() {  # check <expected allow|deny> <description> -- <kubectl args...>
  local expect=$1 what=$2; shift 3
  local out rc
  out=$(kc "$@" --as="$SA" --dry-run=server 2>&1 < "${STDIN:-/dev/null}"); rc=$?
  local got=allow; [[ $rc -ne 0 ]] && got=deny
  local mark=ok; [[ $got == "$expect" ]] && pass=$((pass+1)) || { mark=WRONG; fail=$((fail+1)); }
  printf '%-5s %-6s %s\n' "$mark" "$got" "$what"
  if [[ $got == deny ]]; then
    { grep -o 'denied request: .*\|User "[^"]*" cannot .*' <<< "$out" || head -1 <<< "$out"; } | head -1 | cut -c1-150 | sed 's/^/             /'
  fi
  return 0
}

ns=${STORE_NS:-store-north}
echo "--- namespace label changes on $ns (the provisioner only ever patches annotations)"
check deny  "enforce: baseline -> privileged"  -- patch namespace $ns --type=merge -p '{"metadata":{"labels":{"pod-security.kubernetes.io/enforce":"privileged","pod-security.kubernetes.io/warn":"privileged"}}}'
check deny  "remove the enforce label"          -- patch namespace $ns --type=json -p '[{"op":"remove","path":"/metadata/labels/pod-security.kubernetes.io~1enforce"}]'
check deny  "enforce-version: v1.22"            -- patch namespace $ns --type=merge -p '{"metadata":{"labels":{"pod-security.kubernetes.io/enforce-version":"v1.22"}}}'
check allow "enforce: baseline -> restricted (stricter)" -- patch namespace $ns --type=merge -p '{"metadata":{"labels":{"pod-security.kubernetes.io/enforce":"restricted"}}}'
check allow "annotations only, as reconcile writes them" -- patch namespace $ns --type=merge -p '{"metadata":{"annotations":{"urumi.io/reason":"dry-run probe"}}}'
check deny  "remove the store label"            -- patch namespace $ns --type=json -p '[{"op":"remove","path":"/metadata/labels/urumi.io~1store"}]'

echo "--- new namespaces"
newns() {  # newns <name> <labels as YAML flow map>
  printf 'apiVersion: v1\nkind: Namespace\nmetadata:\n  name: %s\n  labels: %s\n' "$1" "$2" > $scratch/ns.yaml
}
newns store-review-policy-probe '{urumi.io/store: "true", pod-security.kubernetes.io/enforce: privileged}'
STDIN=$scratch/ns.yaml check deny  "store-* namespace, enforce privileged (the review's probe)" -- create -f -
newns store-review-policy-probe '{urumi.io/store: "true"}'
STDIN=$scratch/ns.yaml check deny  "store-* namespace, no enforce label" -- create -f -
newns store-review-policy-probe '{urumi.io/store: "true", urumi.io/store-slug: review-policy-probe, urumi.io/owner: "4", pod-security.kubernetes.io/enforce: baseline, pod-security.kubernetes.io/warn: restricted}'
STDIN=$scratch/ns.yaml check allow "store-* namespace, labelled as cluster.create_namespace does" -- create -f -
newns review-policy-probe '{urumi.io/store: "true", pod-security.kubernetes.io/enforce: baseline}'
STDIN=$scratch/ns.yaml check deny  "name not store-*, baseline" -- create -f -

echo "--- workloads in $ns"
workload() {  # workload <kind> <pod spec as JSON>
  local spec=$2
  case $1 in
    Deployment) printf '{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"review-privilege-proof"},"spec":{"selector":{"matchLabels":{"app":"review-privilege-proof"}},"template":{"metadata":{"labels":{"app":"review-privilege-proof"}},"spec":%s}}}' "$spec" ;;
    StatefulSet) printf '{"apiVersion":"apps/v1","kind":"StatefulSet","metadata":{"name":"review-privilege-proof"},"spec":{"serviceName":"probe","selector":{"matchLabels":{"app":"review-privilege-proof"}},"template":{"metadata":{"labels":{"app":"review-privilege-proof"}},"spec":%s}}}' "$spec" ;;
    Job) printf '{"apiVersion":"batch/v1","kind":"Job","metadata":{"name":"review-privilege-proof"},"spec":{"template":{"spec":%s}}}' "$spec" ;;
  esac > $scratch/workload.json
}
C='{"name":"c","image":"busybox:1.37","command":["sleep","1"]}'
workload Deployment '{"hostPID":true,"containers":[{"name":"proof","image":"busybox:1.37","command":["sh","-c","sleep 1"],"securityContext":{"privileged":true,"runAsUser":0},"volumeMounts":[{"name":"host","mountPath":"/host"}]}],"volumes":[{"name":"host","hostPath":{"path":"/","type":"Directory"}}]}'
STDIN=$scratch/workload.json check deny  "the review's Deployment: hostPID, privileged, hostPath /" -- create -n $ns -f -
workload Deployment "{\"hostNetwork\":true,\"containers\":[$C]}"
STDIN=$scratch/workload.json check deny  "Deployment with hostNetwork"  -- create -n $ns -f -
workload StatefulSet "{\"initContainers\":[{\"name\":\"i\",\"image\":\"busybox:1.37\",\"securityContext\":{\"privileged\":true}}],\"containers\":[$C]}"
STDIN=$scratch/workload.json check deny  "StatefulSet with a privileged init container" -- create -n $ns -f -
workload Job "{\"restartPolicy\":\"Never\",\"containers\":[$C],\"volumes\":[{\"name\":\"h\",\"hostPath\":{\"path\":\"/var/lib\"}}]}"
STDIN=$scratch/workload.json check deny  "Job with a hostPath volume"   -- create -n $ns -f -
workload Deployment "{\"containers\":[$C]}"
STDIN=$scratch/workload.json check allow "ordinary Deployment"          -- create -n $ns -f -
check deny  "a RoleBinding in urumi-app"        -- create rolebinding review-probe -n urumi-app --clusterrole=urumi-store-tenant --serviceaccount=urumi-app:urumi-provisioner

echo "--- the store chart's workloads, as the provisioner installs them"
# Workloads only: the namespace's quota allows 2 volumes, which the store already has.
/tmp/helm-dl/linux-amd64/helm template policy-probe charts/urumi-store -n $ns -f charts/urumi-store/values-tenant.yaml \
  --set credentials.existingSecret=store-credentials --set wordpress.publicUrl=http://policy-probe.example.test \
  --set ingress.host=policy-probe.example.test --set mcp.image.tag=abc --set mcp.gateway.namespace=urumi-app \
  | python3 -c 'import sys; docs = sys.stdin.read().split("\n---\n"); print("\n---\n".join(d for d in docs if any(f"\nkind: {k}\n" in "\n" + d + "\n" for k in ("Deployment", "StatefulSet", "Job"))))' \
  > $scratch/store.yaml
STDIN=$scratch/store.yaml check allow "WordPress, MariaDB, woo-mcp and the setup Job" -- create -n $ns -f -

echo "passed $pass, wrong $fail"
rm -rf "$scratch"
