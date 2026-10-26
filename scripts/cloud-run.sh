#!/usr/bin/env bash
# Deploy the Cloud Run front door (infra/cloud-run): a proxy with a run.app
# address that forwards every request to the cluster's Ingress on the VM.
#
#   bash scripts/cloud-run.sh          build from infra/cloud-run and deploy
#   bash scripts/cloud-run.sh url      print the service's address
#
# The app's PUBLIC_URL (publicUrl in .runtime/app/values-vm.yaml) should be the
# address this prints, so the Connect page and OAuth sign-in name it.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project="${PROJECT:-gen-lang-client-0261050164}" region="${REGION:-us-central1}" service="${SERVICE:-urumi}"
upstream_host="${UPSTREAM_HOST:-app.34-67-189-86.sslip.io}"
number="$(gcloud projects describe "$project" --format='value(projectNumber)')"
public_url="https://$service-$number.$region.run.app"

case "${1:-deploy}" in
  url) echo "$public_url"; exit 0 ;;
  deploy) ;;
  *) echo "usage: $0 [deploy|url]" >&2; exit 2 ;;
esac

# --timeout 3600: chat answers and MCP calls can stream for minutes.
# --min-instances 0: nothing is billed while nobody uses it; the first
# request after a quiet spell waits about a second for the proxy to start.
gcloud run deploy "$service" --project "$project" --region "$region" \
  --source "$root/infra/cloud-run" --allow-unauthenticated \
  --set-env-vars "UPSTREAM=https://$upstream_host,UPSTREAM_HOST=$upstream_host,PUBLIC_URL=$public_url" \
  --cpu 1 --memory 512Mi --concurrency 250 --timeout 3600 \
  --min-instances 0 --max-instances 3 --cpu-boost --quiet
echo "$public_url"
