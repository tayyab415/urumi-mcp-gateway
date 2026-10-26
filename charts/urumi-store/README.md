# Store Helm chart

This chart runs one WooCommerce store: WordPress, its MariaDB database, and a
setup Job that installs WordPress and WooCommerce without any clicks in a
browser. `helm upgrade --install` alone produces a store that takes orders.

## The pieces

- One MariaDB StatefulSet, with a headless internal Service on port 3306.
- One WordPress Deployment, with an internal Service on port 80.
- Two persistent volume claims, one for the database and one for WordPress.
- A ConfigMap for PHP limits and health checks.
- One Ingress for the browser-facing store hostname.
- A setup Job (`<release>-store-setup`), described below.

## The setup Job

Helm runs the Job after every install and upgrade (a `post-install,post-upgrade`
hook). Stores the provisioner makes set `storeSetup.hook: false`
(`values-tenant.yaml`): there the Job is an ordinary part of the release, and
the provisioner watches it. A Job like that which runs out of attempts stays
failed, and a plain `helm upgrade` would leave it so, so the provisioner's
Retry deletes the failed Job first and then upgrades, which creates a fresh
one. The Job uses the official WP-CLI image, mounts the same WordPress volume
as the store, and runs `files/store-setup.sh`:

1. Wait for the WordPress files and the database.
2. Install WordPress if it is not installed yet, with a throwaway admin
   password. Step 6 replaces it with the `wordpress-admin-password` Secret key,
   so the real password never appears in a command line or a log. Rotating it is:
   `python3 scripts/create-store-secret.py --rotate-admin-password`, apply the
   Secret, `helm upgrade`.
3. Set the store title and `/%postname%/` permalinks, which also enable the
   `/wp-json/` REST API path.
4. Install the pinned WooCommerce version and activate it. It upgrades an older
   version and refuses to downgrade a newer one.
5. Switch on WooCommerce's own order tables (HPOS, `wp_wc_orders` and friends).
   A browser install does this on the first admin visit; a CLI install must ask.
6. Run `files/store-setup.php` inside WordPress: the admin password from the
   Secret, country and currency, the coming-soon page off, the shop pages, cash
   on delivery, free shipping, and the catalog.

## The catalog

`storeSetup.catalog` in `values.yaml` lists categories and products. Each
product has a SKU, a category, a price, and either a `stock` count or `sizes`.
A product with sizes becomes a WooCommerce variable product: one parent plus a
variation per size, each with its own SKU (`RM-HOME-2627-M`) and stock. An
optional `image` downloads a photo into the media library; its author and
license go on the store's Image credits page.

The Job adds a catalog once per `version`. Products that already exist (matched
by SKU) are left alone, so edits and deletions made in WordPress admin survive
upgrades. To add products later, append them and bump the version. If a photo
download fails, the version is not marked done and the next run retries it.

The REST key for `woo-mcp` comes from the same Secret. WooCommerce stores only a
hash of a consumer key, so a key made in wp-admin could not be copied into a
Secret afterwards. Instead `create-store-secret.py` writes `woo-consumer-key`
and `woo-consumer-secret`, and step 6 inserts the matching read/write row for
the admin user into `wp_woocommerce_api_keys`. The two keys are optional, so a
Secret made before they existed still installs. Rotating the secret is a Secret
change plus `helm upgrade`.

Each step checks the current state first, so a rerun changes nothing when the
store already matches the values. The Job runs as `www-data` (UID 33), the same
user as WordPress, so WordPress can still update the files it writes. A finished
Job stays until the next upgrade replaces it, so its log remains readable:

```bash
kubectl logs -n urumi-store job/urumi-store-setup
```

WordPress uses a dedicated database account. Its Pod receives only that
account's password, never the database root password. Both passwords come from
an existing Secret in the release namespace. Database root login is restricted
to localhost.

Each workload has startup, readiness, and liveness probes and resource
requests/limits. WordPress readiness checks its database connection; liveness
checks PHP independently of the database. The health route bypasses WordPress
redirects so the installation wizard does not break probes.

The image versions and multi-platform digests were checked against Docker's
registry on 2026-10-21. WordPress uses PHP 8.3 and MariaDB uses the 11.4 LTS line.
Review security updates periodically. An image change requires changing both
the tag and digest; a persisted WordPress installation also needs its own
documented upgrade procedure.

## Check locally without installing

Run from the project root:

```bash
bash scripts/helm.sh lint --strict charts/urumi-store \
  -f charts/urumi-store/values-local.yaml

bash scripts/helm.sh template urumi charts/urumi-store \
  --namespace urumi-store \
  -f charts/urumi-store/values-local.yaml
```

`helm template` prints the Kubernetes objects. It does not create Pods, volumes,
or a store. The output contains Secret references, not generated passwords.

## Deploy after the machine is ready

Before running these commands, confirm the intended Kubernetes context, an
Ingress controller, a working default or named StorageClass, enough free disk
space, and nodes without disk pressure. Allow space for the two 8 GiB volumes
and container images. Local-path storage does not reserve or enforce those
capacities.

For the project's local k3d cluster:

```bash
python3 scripts/create-store-secret.py
bash scripts/kubectl.sh create namespace urumi-store \
  --dry-run=client -o yaml | bash scripts/kubectl.sh apply -f -
bash scripts/kubectl.sh apply -f .runtime/store/credentials.json
bash scripts/helm.sh upgrade --install urumi charts/urumi-store \
  --namespace urumi-store \
  -f charts/urumi-store/values-local.yaml --wait --timeout 10m
bash scripts/kubectl.sh rollout status statefulset/urumi-mariadb \
  --namespace urumi-store
bash scripts/kubectl.sh rollout status deployment/urumi-wordpress \
  --namespace urumi-store
```

On the GCP application VM, `bash scripts/gcp-vm.sh deploy-store` does the
Secret and Helm steps in one command.

The generator writes a file readable only by its owner. It refuses to replace an
existing file, so rerunning it cannot silently change a running database's
password. Keep the file private and outside Git; never paste it into chat.
Kubernetes Secrets also need appropriate access permissions and, where
available, encryption at rest.

## Local URL

The existing k3d cluster forwards its Ingress port 80 to VM loopback port 18080.
Resolve `shop.urumi.test` to `127.0.0.1` on the computer running your browser. For
a Mac accessing this remote VM, also forward the port:

```bash
ssh -L 18080:127.0.0.1:18080 YOUR_VM
```

Open `http://shop.urumi.test:18080`. A host-file entry does not create a tunnel.
The browser must reach the same port in `wordpress.publicUrl`. A quick VM-side
check, after deployment:

```bash
curl --resolve shop.urumi.test:18080:127.0.0.1 \
  http://shop.urumi.test:18080/wp-admin/install.php
```

When changing the hostname or port, change both the Ingress host and public URL
as needed. The chart rejects mismatched hostnames.

## VPS configuration

Copy `values-prod.yaml` to a private, machine-specific values file. Set the real
hostname, installed Ingress class, and StorageClass. Point DNS at the VPS. With
cert-manager installed, the `cert-manager.io/cluster-issuer` annotation in
`values-prod.yaml` gets the certificate into the `urumi-store-tls` Secret and
renews it. Without cert-manager, remove the annotation and create that Secret
yourself. `ingress.redirectToHttps` adds a Traefik middleware that sends plain
HTTP visitors to HTTPS; WordPress does not do this on its own.
`shop.example.com` is an example, not a deployed domain.

The GCP VM runs this way: `https://shop.34-67-189-86.sslip.io`, with values in
`.runtime/store/values-vm.yaml` and a Let's Encrypt certificate (see
`infra/gcp/README.md`). Changing `wordpress.publicUrl` changes WordPress's home
URL; product images and links follow it, because WordPress builds them from
that URL. `woo-mcp` signs its requests for the new host (see the app chart
README).

Use the target machine's Helm and kubectl, with an explicit context:

```bash
kubectl --context YOUR_VPS_CONTEXT create namespace urumi-store \
  --dry-run=client -o yaml | \
  kubectl --context YOUR_VPS_CONTEXT apply -f -
kubectl --context YOUR_VPS_CONTEXT apply -f .runtime/store/credentials.json
helm --kube-context YOUR_VPS_CONTEXT upgrade --install urumi charts/urumi-store \
  --namespace urumi-store -f /path/to/your-vps-values.yaml \
  --wait --timeout 10m
```

Generate fresh credentials on a fresh host. If moving an existing store, restore
its database/content and matching credentials instead. Installing this chart
on another machine does not copy the old store's data.

No cloud load balancer, managed database, or managed Kubernetes service is
required for this single-node setup.

## Turn WordPress into the test store

After the workloads are healthy:

1. Open the WordPress wizard and create the store administrator account.
2. Install and activate the official WooCommerce plugin. Record its version.
3. Set the store timezone deliberately; later, "today's orders" uses it.
4. Use a permalink structure other than Plain for the WooCommerce REST API.
5. Enable cash on delivery or another test payment method.
6. Create one simple virtual product to avoid a shipping dependency in the
   first checkout. Add it to the cart, check out, and find the order in admin.

The PDF does not prescribe a product count. One product/order is our first
verification exercise. Products, orders, and settings live in MariaDB; plugins,
themes, uploads, and the WordPress files live on its content volume.

Before MCP integration, verify WooCommerce API authentication and reachability
separately. The local HTTP example is not an HTTPS endpoint; do not assume that
WooCommerce accepts HTTPS-style Basic authentication on that HTTP connection.

## Persistence and removal

Both PVCs have `helm.sh/resource-policy: keep`, so Helm uninstall preserves them.
This prevents accidental deletion through Helm, but deleting a namespace or PVC
can still destroy data. A future Helm reinstall may require handling the retained
PVCs explicitly; do not assume a fresh install adopts every existing resource.

Local-path volumes remain on their node. Replacing a Pod on that node should
preserve the store; deleting the cluster, losing the node, or changing machines
does not. This is a single-node demo setup, not a highly available database.

Checked live on the GCP VM on 2026-10-25. Order 44 (one tube of shuttles, cash on
delivery, placed through the storefront checkout API) was read back through the
REST key, then the WordPress Pod and, separately, the MariaDB Pod were deleted.
After each replacement the order was still `processing` at the same total and
time, the order count was still 1, WooCommerce was still 11.2.1, and a product
photo from the media library still returned HTTP 200.

## Sources

- [WordPress official image](https://hub.docker.com/_/wordpress)
- [MariaDB image health checks](https://mariadb.com/docs/server/server-management/automated-mariadb-deployment-and-administration/docker-and-mariadb/using-healthcheck-sh)
- [WooCommerce server requirements](https://woocommerce.com/document/server-requirements/)
- [Kubernetes probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
