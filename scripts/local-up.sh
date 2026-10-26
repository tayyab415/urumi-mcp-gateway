#!/usr/bin/env bash
# Run the whole system on a local k3d cluster, with the local values files only.
#
#   bash scripts/local-up.sh          create the cluster, build the images, install both charts
#   bash scripts/local-up.sh cluster  only create the cluster (then apply Secrets yourself)
#   bash scripts/local-up.sh status   show the Pods
#   bash scripts/local-up.sh down     delete the cluster (and everything in it)
#
# Needs docker, k3d, helm and kubectl on PATH. Override any of them, e.g.
# DOCKER="sudo docker". Make the two Secret files first:
#   python3 scripts/create-store-secret.py && python3 scripts/create-app-secret.py
#
# Then put "127.0.0.1 shop.urumi.test app.urumi.test" in /etc/hosts and open
# http://app.urumi.test:18080 (admin@urumi.test, password = admin-password in
# .runtime/app/secrets.json) and http://shop.urumi.test:18080.
#
# Options (environment): CLUSTER (urumi-local), PORT (18080), API_PORT (16443),
# MEMORY (4g), SKIP_SECRETS=1 (apply the Secrets yourself), SKIP_BUILD=1 with
# IMAGES_FILE=<images.yaml from gcp-vm.sh build-images> (reuse built images).
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="${CLUSTER:-urumi-local}" PORT="${PORT:-18080}" API_PORT="${API_PORT:-16443}" MEMORY="${MEMORY:-4g}"
DOCKER="${DOCKER:-docker}" K3D="${K3D:-k3d}" HELM="${HELM:-helm}" KUBECTL="${KUBECTL:-kubectl}"
kubeconfig="${KUBECONFIG_OUT:-$root/.runtime/local-kubeconfig.yaml}"
k() { $KUBECTL --kubeconfig "$kubeconfig" "$@"; }
h() { $HELM --kubeconfig "$kubeconfig" "$@"; }

case "${1:-up}" in
  down) $K3D cluster delete "$CLUSTER"; exit 0 ;;
  status) k get pods -A; exit 0 ;;
  up|cluster) ;;
  *) echo "usage: $0 [up|cluster|status|down]" >&2; exit 2 ;;
esac

echo "== 1. Cluster $CLUSTER (Ingress on 127.0.0.1:$PORT)"
if ! $K3D cluster get "$CLUSTER" >/dev/null 2>&1; then
  $K3D cluster create "$CLUSTER" --image rancher/k3s:v1.36.5-k3s1 --servers 1 --agents 0 \
    --servers-memory "$MEMORY" --api-port "127.0.0.1:$API_PORT" \
    --port "127.0.0.1:$PORT:80@loadbalancer" \
    --kubeconfig-update-default=false --kubeconfig-switch-context=false
fi
mkdir -p "$(dirname "$kubeconfig")"
$K3D kubeconfig get "$CLUSTER" > "$kubeconfig"
chmod 600 "$kubeconfig"
[[ "${1:-up}" == cluster ]] && exit 0  # only the cluster; e.g. to apply the Secrets another way first

echo "== 2. Images"
images=(gateway:services/gateway woo-mcp:services/woo-mcp demo-mcps:services/demo-mcps
        chat-agent:services/chat-agent dashboard:dashboard)
image_args=()
refs=()
if [[ "${SKIP_BUILD:-}" == 1 ]]; then
  : "${IMAGES_FILE:?SKIP_BUILD=1 needs IMAGES_FILE}"
  image_args=(-f "$IMAGES_FILE")
  # Lines look like:  gateway: {repository: urumi/gateway, tag: "abc123"}
  while read -r repo tag; do refs+=("$repo:$tag"); done < <(
    sed -nE 's/.*repository: ([^,]+), tag: "([^"]+)".*/\1 \2/p' "$IMAGES_FILE" | grep -v provisioner)
else
  for entry in "${images[@]}"; do
    name="${entry%%:*}" context="${entry#*:}"
    $DOCKER build -q -t "urumi/$name:local" "$root/$context"
    refs+=("urumi/$name:local")
    key="$(sed -E 's/-(.)/\U\1/g' <<<"$name")"
    image_args+=(--set "images.$key.tag=local")
  done
fi
$K3D image import -c "$CLUSTER" "${refs[@]}"

echo "== 3. Namespaces and Secrets"
k create namespace urumi-store --dry-run=client -o yaml | k apply -f -
k create namespace urumi-app --dry-run=client -o yaml | k apply -f -
k label namespace urumi-app --overwrite pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/warn=restricted
if [[ "${SKIP_SECRETS:-}" != 1 ]]; then
  k apply -f "$root/.runtime/store/credentials.json"
  k apply -f "$root/.runtime/app/secrets.json"
fi

# values-local.yaml uses port 18080. Another port only changes the URLs.
store_port=() app_port=()
if [[ "$PORT" != 18080 ]]; then
  store_port=(--set "wordpress.publicUrl=http://shop.urumi.test:$PORT")
  app_port=(--set "publicUrl=http://app.urumi.test:$PORT" --set "store.publicUrl=http://shop.urumi.test:$PORT")
fi

echo "== 4. Store chart (WordPress, WooCommerce, MariaDB; the setup Job takes a few minutes)"
h upgrade --install urumi "$root/charts/urumi-store" -n urumi-store \
  -f "$root/charts/urumi-store/values-local.yaml" "${store_port[@]}" --wait --timeout 15m

echo "== 5. App chart (gateway, MCP servers, chat, dashboard)"
h upgrade --install urumi "$root/charts/urumi-app" -n urumi-app \
  -f "$root/charts/urumi-app/values-local.yaml" "${image_args[@]}" "${app_port[@]}" --wait --timeout 10m

k get pods -A
cat <<TEXT

Done. Add to /etc/hosts:  127.0.0.1 shop.urumi.test app.urumi.test
Dashboard  http://app.urumi.test:$PORT   (admin@urumi.test, admin-password in .runtime/app/secrets.json)
Store      http://shop.urumi.test:$PORT
MCP        http://app.urumi.test:$PORT/mcp   (OAuth needs https, so use an API key from the Connect page)
TEXT
