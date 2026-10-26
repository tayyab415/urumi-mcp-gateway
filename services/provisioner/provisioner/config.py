"""Settings, read once from environment variables set by the app chart."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Shared with the gateway, from the app Secret. The gateway is the only caller.
    api_token: str
    # Stores get <slug>.<store_domain>, e.g. north.34-67-189-86.sslip.io.
    store_domain: str
    store_admin_email: str
    chart_dir: str
    mcp_image_repository: str
    mcp_image_tag: str
    # The namespace and ServiceAccount the provisioner itself runs as.
    own_namespace: str
    service_account: str
    tenant_cluster_role: str
    # Guardrails.
    max_stores_per_owner: int
    max_stores_total: int
    provision_timeout_seconds: int
    max_helm_attempts: int
    reconcile_interval_seconds: float

    @classmethod
    def from_env(cls) -> Settings:
        token = os.environ.get("PROVISIONER_TOKEN", "")
        if len(token) < 16:
            raise RuntimeError("PROVISIONER_TOKEN must be set to a random string of at least 16 characters")
        return cls(
            api_token=token,
            store_domain=os.environ["STORE_DOMAIN"],
            store_admin_email=os.environ.get("STORE_ADMIN_EMAIL", "admin@urumi.test"),
            chart_dir=os.environ.get("CHART_DIR", "/app/chart"),
            mcp_image_repository=os.environ.get("MCP_IMAGE_REPOSITORY", "urumi/woo-mcp"),
            mcp_image_tag=os.environ["MCP_IMAGE_TAG"],
            own_namespace=os.environ.get("POD_NAMESPACE", "urumi-app"),
            service_account=os.environ.get("SERVICE_ACCOUNT", "urumi-provisioner"),
            tenant_cluster_role=os.environ.get("TENANT_CLUSTER_ROLE", "urumi-store-tenant"),
            max_stores_per_owner=int(os.environ.get("MAX_STORES_PER_OWNER", "2")),
            max_stores_total=int(os.environ.get("MAX_STORES_TOTAL", "4")),
            provision_timeout_seconds=int(os.environ.get("PROVISION_TIMEOUT_SECONDS", "900")),
            max_helm_attempts=int(os.environ.get("MAX_HELM_ATTEMPTS", "3")),
            reconcile_interval_seconds=float(os.environ.get("RECONCILE_INTERVAL_SECONDS", "5")),
        )
