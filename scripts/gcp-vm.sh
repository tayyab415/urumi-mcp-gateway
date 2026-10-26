#!/usr/bin/env bash
# Controls only the separate Urumi application VM.
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project="gen-lang-client-0261050164"
zone="us-central1-a"
instance="urumi-app"
ssh_key="$project_root/.runtime/gcp/ssh-key"
common=(--project="$project" --zone="$zone")

usage() {
  cat <<'TEXT'
Usage: bash scripts/gcp-vm.sh COMMAND

  status         Show application VM status and current public IP.
  start          Start the VM and wait for the Kubernetes API.
  stop           Stop the VM. Its persistent disk remains.
  ready          Wait for the Kubernetes API on a running VM.
  ssh            Open a shell on the VM.
  run COMMAND    Run a command on the VM.
  kube ARGS      Run kubectl against the VM's cluster.
  helm ARGS      Run Helm against the VM's cluster.
  tunnel         Forward local port 18081 to the VM's store ingress.
  deploy-store   Apply the store Secret, copy the chart, and run helm upgrade.
  build-images   Build the six app images on the VM and import them into k3d.
  deploy-app     Apply the app namespace and Secret, copy the chart, run helm upgrade.
  install-cert-manager
                 Install cert-manager and the Let's Encrypt issuers (TLS certificates).

These commands control urumi-app only, not the coding workspace.
TEXT
}

wait_until_ready() {
  echo "Waiting for Kubernetes to finish starting..."
  for attempt in {1..18}; do
    if gcloud compute ssh "urumi@$instance" "${common[@]}" \
      --ssh-key-file="$ssh_key" --quiet \
      --ssh-flag=-oConnectTimeout=5 --ssh-flag=-oConnectionAttempts=1 \
      --command='sudo env KUBECONFIG=/srv/urumi/tools/.runtime/kubernetes/kubeconfig.yaml /srv/urumi/tools/.runtime/bin/kubectl get --raw=/readyz --request-timeout=5s' \
      >/dev/null 2>&1
    then
      echo "Kubernetes API is ready."
      return 0
    fi
    sleep 5
  done
  echo "Kubernetes is not ready yet. Check the VM and urumi-cluster.service." >&2
  return 1
}

if [[ $# -eq 0 ]]; then
  usage
  exit 0
fi

action="$1"
shift
case "$action" in
  status)
    gcloud compute instances describe "$instance" "${common[@]}" \
      --format='table(name,status,machineType.basename(),networkInterfaces[0].accessConfigs[0].natIP)'
    ;;
  start|stop)
    # Refuse to operate on a VM with an unexpected owner label.
    purpose="$(gcloud compute instances describe "$instance" "${common[@]}" \
      --format='value(labels.purpose)')"
    if [[ "$purpose" != "urumi-assignment" ]]; then
      echo "Refusing to $action a VM without the urumi-assignment label." >&2
      exit 1
    fi
    gcloud compute instances "$action" "$instance" "${common[@]}" --quiet
    if [[ "$action" == "start" ]]; then
      wait_until_ready
    fi
    ;;
  ready)
    wait_until_ready
    ;;
  ssh)
    gcloud compute ssh "urumi@$instance" "${common[@]}" \
      --ssh-key-file="$ssh_key"
    ;;
  run)
    if [[ $# -ne 1 ]]; then
      echo 'Pass the remote command as one quoted argument.' >&2
      exit 2
    fi
    gcloud compute ssh "urumi@$instance" "${common[@]}" \
      --ssh-key-file="$ssh_key" --command="$1"
    ;;
  kube|helm)
    tool="$action"
    if [[ "$tool" == "kube" ]]; then
      tool="kubectl"
    fi
    printf -v remote_args '%q ' "$@"
    gcloud compute ssh "urumi@$instance" "${common[@]}" \
      --ssh-key-file="$ssh_key" \
      --command="sudo env KUBECONFIG=/srv/urumi/tools/.runtime/kubernetes/kubeconfig.yaml /srv/urumi/tools/.runtime/bin/$tool $remote_args"
    ;;
  tunnel)
    gcloud compute ssh "urumi@$instance" "${common[@]}" \
      --ssh-key-file="$ssh_key" -- \
      -N -L 127.0.0.1:18081:127.0.0.1:18080 \
      -o ExitOnForwardFailure=yes -o ServerAliveInterval=30
    ;;
  deploy-store)
    # The Secret goes over SSH stdin; it is never written to the VM's disk.
    credentials="$project_root/.runtime/store/credentials.json"
    vm_values="$project_root/.runtime/store/values-vm.yaml"
    bash "$0" kube apply -f - < "$credentials"
    gcloud compute ssh "urumi@$instance" "${common[@]}" \
      --ssh-key-file="$ssh_key" --command='rm -rf ~/deploy && mkdir -p ~/deploy'
    gcloud compute scp --recurse "${common[@]}" --ssh-key-file="$ssh_key" \
      "$project_root/charts/urumi-store" "$vm_values" "urumi@$instance:deploy/"
    bash "$0" helm upgrade --install urumi /home/urumi/deploy/urumi-store \
      --namespace urumi-store \
      -f /home/urumi/deploy/urumi-store/values-local.yaml \
      -f /home/urumi/deploy/values-vm.yaml \
      --wait --timeout 15m
    ;;
  deploy-app)
    # The namespace enforces the "restricted" Pod Security Standard: Kubernetes
    # refuses any Pod here that could run as root or keep Linux capabilities.
    bash "$0" kube apply -f - <<'YAML'
apiVersion: v1
kind: Namespace
metadata:
  name: urumi-app
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/warn: restricted
YAML
    # The Secret goes over SSH stdin; it is never written to the VM's disk.
    bash "$0" kube apply -f - < "$project_root/.runtime/app/secrets.json"
    gcloud compute ssh "urumi@$instance" "${common[@]}" \
      --ssh-key-file="$ssh_key" --command='rm -rf ~/deploy-app && mkdir -p ~/deploy-app'
    gcloud compute scp --recurse "${common[@]}" --ssh-key-file="$ssh_key" \
      "$project_root/charts/urumi-app" "$project_root/.runtime/app/images.yaml" \
      "$project_root/.runtime/app/values-vm.yaml" "urumi@$instance:deploy-app/"
    bash "$0" helm upgrade --install urumi /home/urumi/deploy-app/urumi-app \
      --namespace urumi-app \
      -f /home/urumi/deploy-app/urumi-app/values-local.yaml \
      -f /home/urumi/deploy-app/images.yaml \
      -f /home/urumi/deploy-app/values-vm.yaml \
      --wait --timeout 10m "$@"
    ;;
  install-cert-manager)
    # cert-manager gets and renews TLS certificates for Ingresses that ask for
    # one. crds.enabled installs its resource types (Certificate, ClusterIssuer).
    bash "$0" helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager \
      --version v1.21.3 --namespace cert-manager --create-namespace \
      --set crds.enabled=true --wait --timeout 5m
    bash "$0" kube apply -f - < "$project_root/infra/cluster/cluster-issuers.yaml"
    ;;
  build-images)
    # Build on the VM and import into k3d, so no registry is needed. Each tag is a
    # hash of that image's source: changed code gets a new tag, so helm upgrade
    # replaces the Pods, and an unchanged image keeps its tag.
    images=(gateway:services/gateway woo-mcp:services/woo-mcp demo-mcps:services/demo-mcps
            chat-agent:services/chat-agent dashboard:dashboard slack-mcp:services/slack-mcp)
    staging="$(mktemp -d)"
    trap 'rm -rf "$staging"' EXIT
    tar_flags=(--sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner
               --exclude=.venv --exclude=node_modules --exclude=dist --exclude=__pycache__
               --exclude=.pytest_cache --exclude=.ruff_cache --exclude='*.db')
    remote='set -euo pipefail; rm -rf ~/build; mkdir -p ~/build; tar -xzf ~/build.tgz -C ~/build; rm ~/build.tgz; cd ~/build'
    refs=()
    values="images:"
    for entry in "${images[@]}"; do
      name="${entry%%:*}" context="${entry#*:}"
      tar -C "$project_root/$context" "${tar_flags[@]}" -cf "$staging/$name.tar" .
      tag="$(sha256sum "$staging/$name.tar" | cut -c1-12)"
      mkdir "$staging/src-$name"
      tar -xf "$staging/$name.tar" -C "$staging/src-$name"
      remote+="; echo Building urumi/$name:$tag; sudo docker build -q -t urumi/$name:$tag $name"
      refs+=("urumi/$name:$tag")
      # Helm values keys are camelCase (woo-mcp -> wooMcp); a hyphen breaks .Values.images.x.
      key="$(sed -E 's/-(.)/\U\1/g' <<<"$name")"
      values+=$'\n'"  $key: {repository: urumi/$name, tag: \"$tag\"}"
    done
    for entry in "${images[@]}"; do
      name="${entry%%:*}"
      mv "$staging/src-$name" "$staging/$name"
      rm "$staging/$name.tar"
    done
    tar -C "$staging" -czf "$staging/build.tgz" "${images[@]%%:*}"
    gcloud compute scp "${common[@]}" --ssh-key-file="$ssh_key" \
      "$staging/build.tgz" "urumi@$instance:build.tgz"
    remote+="; sudo /srv/urumi/tools/.runtime/bin/k3d image import -c urumi-vm ${refs[*]}"
    gcloud compute ssh "urumi@$instance" "${common[@]}" \
      --ssh-key-file="$ssh_key" --command="$remote"
    mkdir -p "$project_root/.runtime/app"
    printf '%s\n' "# Written by: bash scripts/gcp-vm.sh build-images" "$values" \
      > "$project_root/.runtime/app/images.yaml"
    echo "Image tags written to .runtime/app/images.yaml"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
