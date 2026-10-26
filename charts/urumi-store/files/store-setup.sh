#!/bin/sh
# Brings WordPress and WooCommerce to the state described by the Helm values.
# Every step checks the current state first, so running it again is safe.
set -eu
cd /var/www/html

wait_for() {
  description="$1"
  shift
  attempt=0
  until "$@" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 120 ]; then
      echo "Timed out waiting for $description." >&2
      exit 1
    fi
    sleep 5
  done
  echo "Ready: $description."
}

database_reachable() {
  php -r '
    mysqli_report(MYSQLI_REPORT_OFF);
    $db = mysqli_init();
    $db->options(MYSQLI_OPT_CONNECT_TIMEOUT, 3);
    exit(@$db->real_connect(getenv("WORDPRESS_DB_HOST"), getenv("WORDPRESS_DB_USER"),
      getenv("WORDPRESS_DB_PASSWORD"), getenv("WORDPRESS_DB_NAME")) ? 0 : 1);'
}

# The WordPress container copies its files and writes wp-config.php on first start.
wait_for "WordPress files" test -f wp-config.php
wait_for "the database" database_reachable

if wp core is-installed; then
  echo "WordPress is already installed."
else
  # A throwaway password: store-setup.php sets the real one from the Secret,
  # so it never appears in a command line or a log.
  wp core install \
    --url="$WORDPRESS_PUBLIC_URL" --title="$STORE_TITLE" \
    --admin_user="$STORE_ADMIN_USER" --admin_email="$STORE_ADMIN_EMAIL" \
    --admin_password="$(php -r 'echo bin2hex(random_bytes(24));')" --skip-email
fi

[ "$(wp option get blogname)" = "$STORE_TITLE" ] || wp option update blogname "$STORE_TITLE"

# Readable URLs such as /product/mug/, and the /wp-json/ path for the REST API.
if [ "$(wp option get permalink_structure)" != "/%postname%/" ]; then
  wp rewrite structure "/%postname%/"
fi

current="$(wp plugin get woocommerce --field=version 2>/dev/null || true)"
if [ -z "$current" ] || php -r 'exit(version_compare($argv[1], $argv[2], "<") ? 0 : 1);' "$current" "$WOOCOMMERCE_VERSION"; then
  echo "Installing WooCommerce $WOOCOMMERCE_VERSION (found: ${current:-none})."
  wp plugin install woocommerce --version="$WOOCOMMERCE_VERSION" --force
elif [ "$current" != "$WOOCOMMERCE_VERSION" ]; then
  echo "WooCommerce $current is newer than $WOOCOMMERCE_VERSION; not downgrading."
fi
wp plugin is-active woocommerce || wp plugin activate woocommerce
wp wc update

# Keep orders in WooCommerce's own tables (HPOS), the default for new stores.
# A browser install switches this on during the first admin visit, so a CLI
# install has to do it. On a store with old unsynced orders this fails loudly.
if [ "$(wp option get woocommerce_custom_orders_table_enabled)" != "yes" ]; then
  wp wc hpos enable
fi

wp eval-file /setup/store-setup.php
echo "Store setup finished."
