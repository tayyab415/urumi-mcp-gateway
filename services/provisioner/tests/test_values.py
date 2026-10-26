from provisioner import cluster
from provisioner.config import Settings


def settings() -> Settings:
    return Settings(
        api_token="t" * 20, store_domain="34-67-189-86.sslip.io", store_admin_email="admin@urumi.test",
        chart_dir="/app/chart", mcp_image_repository="urumi/woo-mcp", mcp_image_tag="abc123",
        own_namespace="urumi-app", service_account="urumi-provisioner", tenant_cluster_role="urumi-store-tenant",
        max_stores_per_owner=2, max_stores_total=4, provision_timeout_seconds=900, max_helm_attempts=3,
        reconcile_interval_seconds=5,
    )


def test_store_values_pin_host_url_and_gateway():
    v = cluster.store_values("north", 'North "Shop", Pune', settings())
    assert v["ingress"]["host"] == "north.34-67-189-86.sslip.io"
    assert v["wordpress"]["publicUrl"] == "http://north.34-67-189-86.sslip.io"
    assert v["storeSetup"]["title"] == 'North "Shop", Pune'  # goes in a file, so commas and quotes survive
    assert v["mcp"]["image"]["tag"] == "abc123"
    assert v["mcp"]["gateway"]["namespace"] == "urumi-app"


def test_new_credentials_have_woocommerce_key_shapes():
    c = cluster.new_credentials()
    assert c["woo-consumer-key"].startswith("ck_") and len(c["woo-consumer-key"]) == 43
    assert c["woo-consumer-secret"].startswith("cs_") and len(c["woo-consumer-secret"]) == 43
    assert c != cluster.new_credentials()
