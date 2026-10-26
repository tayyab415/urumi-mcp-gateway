# The application VM

This VM holds the Urumi application and Kubernetes cluster. The coding workspace
remains on `t3-agent-1`. Stopping the application VM does not stop T3.

| Setting | Value |
| --- | --- |
| Project | `gen-lang-client-0261050164` |
| VM | `urumi-app` |
| Zone | `us-central1-a`, Iowa |
| Machine | `e2-standard-2`, 2 vCPUs, 8 GiB RAM |
| OS | Ubuntu 24.04 LTS |
| Disk | 40 GiB balanced Persistent Disk |
| Public IP | `34.67.189.86`, static (`urumi-app-ip`), kept while stopped |
| Cluster | `urumi-vm`, k3d with one K3s server |
| Ingress | public ports `80` and `443`, and VM loopback port `18080` |
| App | https://urumi-1027824348124.us-central1.run.app (Cloud Run proxy, [infra/cloud-run](../cloud-run/Caddyfile)), or https://app.34-67-189-86.sslip.io directly |
| Store | https://shop.34-67-189-86.sslip.io |
| Kubernetes API | VM loopback port `16443` |

## Everyday controls

Run these commands from the repo root on the coding workspace:

```bash
bash scripts/gcp-vm.sh status
bash scripts/gcp-vm.sh start
bash scripts/gcp-vm.sh kube get nodes
bash scripts/gcp-vm.sh stop
```

Starting takes time for Ubuntu, Docker and Kubernetes to boot. The helper waits
for the Kubernetes API before returning. Application Pods may need a little
longer to become ready. The `urumi-cluster.service` systemd service starts the
existing cluster during boot. `start` does not reinstall the application.

In Google Cloud Console, select this project, open **Compute Engine → VM
instances**, and start or stop **urumi-app**. Select **Stop**, not **Delete**.

The helper uses the VM name to look up its current IP. It does not depend on
the IP staying the same. SSH currently accepts the coding workspace's public IP
and the Google IAP range. If the coding workspace's public IP changes, update
the `urumi-allow-ssh` firewall rule's source range.

## What stopping does

Stopping shuts down the VM. The store and chat will be unavailable while it is
off. CPU and memory billing stops. The disk remains, with its files and
Kubernetes state. Data in RAM and requests in progress do not survive.

Kubernetes local-path volumes use `/srv/urumi/storage` on that disk. Stopping the
VM preserves this directory. Deleting a Kubernetes PVC, deleting the cluster,
or removing data from the disk is a different operation and can lose data.

k3d drains its node during shutdown, removing application Pods. Deployments
and StatefulSets recreate their Pods after startup and reconnect their
persistent volumes. A standalone Pod has no controller to recreate it.
The WordPress and MariaDB chart already uses these controllers.

The boot disk is also configured to remain if the VM is deleted. It keeps
incurring storage charges until the disk itself is deleted.

## Costs

These are estimates from public Iowa prices checked on 2026-10-21, before
credits, tax, transfer, snapshots or model API usage.

- Compute costs about `$0.06701142` per running hour.
- The public IPv4 costs `$0.005` per hour. Since 2026-10-26 it is a static IP.
  Google counts a static IP attached to a VM as in use even while the VM is
  stopped, so this charge continues while stopped: about `$3.65` per month.
  Reserved but detached from the VM, it would cost `$0.01` per hour.
- The 40 GiB balanced disk costs about `$4` per month, even while stopped.
- Running 24 hours daily costs about `$1.86` per day in total.
- Running 8 hours daily for 30 days costs about `$21.23` in total.
- Leaving it stopped for a month costs about `$4` for the disk.

Credit coverage remains unverified. Cloud Billing API is disabled or
inaccessible to the coding workspace's service account. This VM uses standard
on-demand pricing, with no reservation or committed-use purchase.

Stopping is manual. No automatic stop schedule has been installed.

Sources:

- https://cloud.google.com/products/compute/pricing/general-purpose
- https://cloud.google.com/compute/disks-image-pricing
- https://cloud.google.com/vpc/network-pricing
- https://docs.cloud.google.com/compute/docs/instances/stop-start-instance

## Files and access

The SSH private key is in `.runtime/gcp/ssh-key` on the coding workspace.
Do not share it. Kubernetes credentials stay on the application VM in
`/srv/urumi/tools/.runtime/kubernetes/kubeconfig.yaml`, readable only by root.
The helper runs the Kubernetes tools through `sudo` over SSH.

The VM has its own VPC, `urumi-net`, with two firewall rules. It has no
attached Google service account. The Kubernetes API is reachable only through
SSH.

| Rule | Allows | From |
| --- | --- | --- |
| `urumi-allow-ssh` | TCP 22 | the coding workspace's IP and Google's IAP range |
| `urumi-allow-web` | TCP 80, 443 | anywhere |

## Public access

Added on 2026-10-26, so reviewers, Claude Code and Cursor can reach the app:

1. The VM's address became static:
   `gcloud compute addresses create urumi-app-ip --region=us-central1 --addresses=34.67.189.86`.
   Hostnames and certificates name this IP, so it must not change on restart.
2. The firewall rule `urumi-allow-web` opens TCP 80 and 443.
3. k3d publishes ports 80 and 443 on the VM:
   `k3d cluster edit urumi-vm --port-add 80:80@loadbalancer --port-add 443:443@loadbalancer`.
   This replaces only k3d's load balancer container; the cluster keeps running.
   `configure-cluster.sh` has the same ports for a new cluster.
4. `bash scripts/gcp-vm.sh install-cert-manager` installs cert-manager v1.21.3
   and the `letsencrypt` issuers in `infra/cluster/cluster-issuers.yaml`.
5. The values files in `.runtime/app/` and `.runtime/store/` name the sslip.io
   hostnames and turn TLS on. sslip.io's DNS answers
   `anything.34-67-189-86.sslip.io` with `34.67.189.86`, so no DNS setup is
   needed.

To close public access again, delete `urumi-allow-web`. The app then works
only through the tunnel.

Once the store is deployed, this opens a store tunnel on the coding workspace:

```bash
bash scripts/gcp-vm.sh tunnel
```

It forwards workspace port `18081` to VM port `18080`. Access from a laptop
needs an additional connection to the coding workspace. The current local
cluster continues to use workspace port `18080`. Since the move to public
hostnames, the tunnel reaches Traefik but no Ingress answers to
`shop.urumi.test` or `app.urumi.test`; use the public URLs.

## Deploying the store

```bash
bash scripts/gcp-vm.sh deploy-store
```

This applies `.runtime/store/credentials.json` as a Secret over SSH (the file is
never copied to the VM), copies `charts/urumi-store` and
`.runtime/store/values-vm.yaml` to `~/deploy` on the VM, and runs
`helm upgrade --install` with the local and VM values. Helm then runs the
store-setup Job, which installs WordPress and WooCommerce. The store is at
https://shop.34-67-189-86.sslip.io.

## Verification on 2026-10-21

Two real VM stop/start cycles confirmed that the disk and Kubernetes state
survive. A test Deployment recreated its Pod after boot and read the original
file from the same persistent volume. Docker and Kubernetes started without
manual intervention. The public IP changed during the second restart, and
the name-based controls still worked.

The node had no disk pressure and about 33 GiB free after setup. The coding
workspace's boot ID remained unchanged throughout the test. The temporary
test namespace and volume were removed after verification.

The application VM was left stopped at 13:03 UTC on 2026-10-21. GCP confirmed
`TERMINATED`, no attached public IPv4, and a retained 40 GiB disk. Use `status`
to check its current state.

The local evidence is `.runtime/gcp/stop-start-verification.json`. This checks
the infrastructure with a small test workload. WordPress, MariaDB, checkout
and order persistence still need their own checks after deployment.
