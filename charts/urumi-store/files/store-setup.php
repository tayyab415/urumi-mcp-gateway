<?php
// Runs inside WordPress through `wp eval-file`, after WooCommerce is active.

// The Secret is the source of truth for the admin password, so rotating it is:
// change the Secret, then helm upgrade.
$admin = get_user_by('login', getenv('STORE_ADMIN_USER'));
if (!$admin) {
    WP_CLI::error('Admin user ' . getenv('STORE_ADMIN_USER') . ' not found.');
}
if (!wp_check_password(getenv('STORE_ADMIN_PASSWORD'), $admin->user_pass, $admin->ID)) {
    wp_set_password(getenv('STORE_ADMIN_PASSWORD'), $admin->ID);
    WP_CLI::log('Set the admin password from the Secret.');
}

// REST API key for woo-mcp. WooCommerce stores only a hash of the consumer key,
// so a key made in wp-admin could never be copied into a Secret afterwards. The
// Secret holds the key instead, and this row is made to match it.
$consumer_key = getenv('STORE_API_CONSUMER_KEY');
$consumer_secret = getenv('STORE_API_CONSUMER_SECRET');
if ($consumer_key && $consumer_secret) {
    global $wpdb;
    $table = $wpdb->prefix . 'woocommerce_api_keys';
    $hash = wc_api_hash($consumer_key);
    $row = $wpdb->get_row($wpdb->prepare("SELECT key_id, consumer_secret FROM $table WHERE consumer_key = %s", $hash));
    if (!$row) {
        $wpdb->insert($table, [
            'user_id' => $admin->ID,
            'description' => 'Urumi MCP gateway (woo-mcp)',
            'permissions' => 'read_write',
            'consumer_key' => $hash,
            'consumer_secret' => $consumer_secret,
            'truncated_key' => substr($consumer_key, -7),
        ]);
        WP_CLI::log('Created the REST API key for woo-mcp.');
    } elseif ($row->consumer_secret !== $consumer_secret) {
        $wpdb->update($table, ['consumer_secret' => $consumer_secret], ['key_id' => $row->key_id]);
        WP_CLI::log('Updated the REST API secret from the Secret.');
    }
}

$settings = [
    'woocommerce_default_country' => getenv('STORE_COUNTRY'),
    'woocommerce_currency' => getenv('STORE_CURRENCY'),
    // New WooCommerce stores start hidden behind a "coming soon" page.
    'woocommerce_coming_soon' => 'no',
];
foreach ($settings as $name => $value) {
    if (get_option($name) !== $value) {
        update_option($name, $value);
        WP_CLI::log("Set $name to $value.");
    }
}

// Skip the admin setup wizard; the values above already answer it.
if (!get_option('woocommerce_onboarding_profile')) {
    update_option('woocommerce_onboarding_profile', ['skipped' => true]);
}

// Shop, cart, checkout and my-account pages.
if (get_post_status(wc_get_page_id('checkout')) !== 'publish') {
    WC_Install::create_pages();
    WP_CLI::log('Created the WooCommerce pages.');
}

// Cash on delivery: the test-friendly payment method the brief allows.
$cod = get_option('woocommerce_cod_settings', []);
if (($cod['enabled'] ?? 'no') !== 'yes') {
    update_option('woocommerce_cod_settings', array_merge([
        'title' => 'Cash on delivery',
        'description' => 'Pay with cash upon delivery.',
        'instructions' => 'Pay with cash upon delivery.',
        'enable_for_methods' => [],
        'enable_for_virtual' => 'yes',
    ], $cod, ['enabled' => 'yes']));
    WP_CLI::log('Enabled cash on delivery.');
}

// Free shipping everywhere, so checkout always offers a shipping option.
$zone = new WC_Shipping_Zone(0);
if (!in_array('free_shipping', wp_list_pluck($zone->get_shipping_methods(), 'id'), true)) {
    $zone->add_shipping_method('free_shipping');
    WP_CLI::log('Added free shipping for all locations.');
}

// The catalog is added once per version, so deleted products stay deleted.
// A version stays unfinished (and is retried next run) if a photo download failed.
$catalog = json_decode(file_get_contents('/setup/catalog.json'), true);
if (get_option('urumi_catalog_version') !== $catalog['version']) {
    require_once ABSPATH . 'wp-admin/includes/media.php';
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/image.php';
    // Wikimedia asks for a descriptive User-Agent on downloads.
    add_filter('http_headers_useragent', fn() => 'UrumiSportsSetup/1.0 (WooCommerce demo store; ' . home_url() . ')');

    $category_ids = [];
    foreach ($catalog['categories'] as $name) {
        $term = term_exists($name, 'product_cat') ?: wp_insert_term($name, 'product_cat');
        if (is_wp_error($term)) {
            WP_CLI::error("Could not create category $name: " . $term->get_error_message());
        }
        $category_ids[$name] = (int) $term['term_id'];
    }

    $complete = true;
    foreach ($catalog['products'] as $item) {
        $product_id = wc_get_product_id_by_sku($item['sku']);
        if (!$product_id) {
            $product_id = urumi_create_product($item, $category_ids[$item['category']]);
            WP_CLI::log("Added {$item['name']}.");
        }
        $product = wc_get_product($product_id);
        if (!empty($item['image']) && !$product->get_image_id()) {
            $image_id = media_sideload_image($item['image']['url'], $product_id, $item['name'], 'id');
            if (is_wp_error($image_id)) {
                WP_CLI::warning("Photo for {$item['name']} failed: " . $image_id->get_error_message());
                $complete = false;
                continue;
            }
            $product->set_image_id($image_id);
            $product->save();
        }
    }
    urumi_update_credits_page($catalog['products']);
    if ($complete) {
        update_option('urumi_catalog_version', $catalog['version']);
        WP_CLI::log("Catalog {$catalog['version']} is in place.");
    }
}

function urumi_create_product(array $item, int $category_id): int
{
    $sizes = $item['sizes'] ?? [];
    $product = $sizes ? new WC_Product_Variable() : new WC_Product_Simple();
    $product->set_name($item['name']);
    $product->set_sku($item['sku']);
    $product->set_description($item['description'] ?? '');
    $product->set_category_ids([$category_id]);
    $product->set_status('publish');
    if (!$sizes) {
        $product->set_regular_price($item['price']);
        $product->set_manage_stock(true);
        $product->set_stock_quantity($item['stock']);
        return $product->save();
    }

    // A sized product is a parent row plus one variation row per size,
    // each with its own SKU and stock count.
    $attribute = new WC_Product_Attribute();
    $attribute->set_name('Size');
    $attribute->set_options(array_column($sizes, 'size'));
    $attribute->set_visible(true);
    $attribute->set_variation(true);
    $product->set_attributes([$attribute]);
    $parent_id = $product->save();
    foreach ($sizes as $size) {
        $variation = new WC_Product_Variation();
        $variation->set_parent_id($parent_id);
        $variation->set_attributes(['size' => $size['size']]);
        $variation->set_sku($item['sku'] . '-' . preg_replace('/[^A-Z0-9]/', '', strtoupper($size['size'])));
        $variation->set_regular_price($item['price']);
        $variation->set_manage_stock(true);
        $variation->set_stock_quantity($size['stock']);
        $variation->save();
    }
    WC_Product_Variable::sync($parent_id);
    return $parent_id;
}

// CC BY and CC BY-SA photos need visible credit; this page lists it.
function urumi_update_credits_page(array $products): void
{
    $items = '';
    foreach ($products as $item) {
        if (empty($item['image'])) {
            continue;
        }
        $image = $item['image'];
        $items .= sprintf(
            '<li>%s: photo by %s, <a href="%s">%s</a>, from <a href="%s">Wikimedia Commons</a></li>',
            esc_html($item['name']), esc_html($image['author']), esc_url($image['licenseUrl']),
            esc_html($image['license']), esc_url($image['source'])
        );
    }
    if ($items === '') {
        return;
    }
    $content = "<p>Product photos are used under these licenses. They show the kind of item, not the exact product.</p>\n<ul>$items</ul>";
    $page = get_page_by_path('image-credits');
    if (!$page) {
        wp_insert_post(['post_type' => 'page', 'post_status' => 'publish', 'post_name' => 'image-credits',
                        'post_title' => 'Image credits', 'post_content' => $content]);
    } elseif ($page->post_content !== $content) {
        wp_update_post(['ID' => $page->ID, 'post_content' => $content]);
    }
}
