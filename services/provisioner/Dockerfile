FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
# Helm runs as a subprocess with the Pod's ServiceAccount. Pinned, and checked
# against the published checksum.
ARG HELM_VERSION=v4.3.1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSLo /tmp/helm.tgz https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz \
 && curl -fsSLo /tmp/helm.tgz.sha256sum https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz.sha256sum \
 && cd /tmp && echo "$(cut -d' ' -f1 helm.tgz.sha256sum)  helm.tgz" | sha256sum -c - \
 && tar -xzf helm.tgz && mv linux-amd64/helm /usr/local/bin/helm && rm -rf /tmp/* \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    HELM_CACHE_HOME=/tmp/helm/cache HELM_CONFIG_HOME=/tmp/helm/config HELM_DATA_HOME=/tmp/helm/data
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY provisioner ./provisioner
RUN uv sync --frozen --no-dev
# The store chart, copied in by `gcp-vm.sh build-images`: every store is installed from it.
COPY chart ./chart
USER 10001
EXPOSE 8080
CMD [".venv/bin/uvicorn", "provisioner.app:app", "--host", "0.0.0.0", "--port", "8080"]
