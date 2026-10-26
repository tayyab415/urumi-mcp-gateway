#!/usr/bin/env python3
"""Install checksum-verified Kubernetes CLIs inside this learning workspace."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
BIN = RUNTIME / "bin"
# Kubernetes matches the k3s stable channel checked on 2026-10-21.
VERSIONS = {
    "kubectl": "v1.36.5",
    "helm": "v4.3.1",
    "k3d": "v5.9.0",
    "planned_k3s": "v1.36.5-k3s1",
}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "urumi-learning"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def install(name: str, url: str, checksum_url: str, member: str | None = None) -> dict:
    checksum_text = fetch(checksum_url).decode()
    filename = url.rsplit("/", 1)[1]
    if name == "k3d":
        matches = [
            line.split()[0]
            for line in checksum_text.splitlines()
            if len(line.split()) == 2
            and Path(line.split()[1].lstrip("*")).name == filename
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Missing or ambiguous checksum for {filename}")
        expected = matches[0]
    else:
        expected = checksum_text.split()[0]
    if len(expected) != 64:
        raise RuntimeError(f"Invalid checksum for {filename}")
    payload = fetch(url)
    if sha256(payload) != expected:
        raise RuntimeError(f"Checksum mismatch for {filename}")
    if member:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            entry = archive.getmember(member)
            if not entry.isfile():
                raise RuntimeError(f"Not a regular file: {member}")
            source = archive.extractfile(entry)
            if source is None:
                raise RuntimeError(f"Could not read {member}")
            executable = source.read()
    else:
        executable = payload
    target = BIN / name
    with tempfile.NamedTemporaryFile(dir=BIN, prefix=f".{name}-", delete=False) as temp:
        temporary = Path(temp.name)
        try:
            temp.write(executable)
            temp.flush()
            os.fchmod(temp.fileno(), 0o755)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Installed {name} {VERSIONS[name]} with verified download checksum.")
    return {
        "version": VERSIONS[name],
        "url": url,
        "download_sha256": expected,
        "binary_sha256": sha256(executable),
    }


def main() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("This setup currently targets Linux x86_64.")
    if shutil.disk_usage(ROOT).free < 512 * 1024**2:
        raise SystemExit("At least 512 MiB of free disk space is needed for tool setup.")
    BIN.mkdir(parents=True, exist_ok=True)
    kubectl = VERSIONS["kubectl"]
    helm = VERSIONS["helm"]
    k3d = VERSIONS["k3d"]
    results = {
        "kubectl": install(
            "kubectl",
            f"https://dl.k8s.io/release/{kubectl}/bin/linux/amd64/kubectl",
            f"https://dl.k8s.io/release/{kubectl}/bin/linux/amd64/kubectl.sha256",
        ),
        "helm": install(
            "helm",
            f"https://get.helm.sh/helm-{helm}-linux-amd64.tar.gz",
            f"https://get.helm.sh/helm-{helm}-linux-amd64.tar.gz.sha256sum",
            "linux-amd64/helm",
        ),
        "k3d": install(
            "k3d",
            f"https://github.com/k3d-io/k3d/releases/download/{k3d}/k3d-linux-amd64",
            f"https://github.com/k3d-io/k3d/releases/download/{k3d}/checksums.txt",
        ),
    }
    results["planned_k3s"] = VERSIONS["planned_k3s"]
    (RUNTIME / "kubernetes-tools.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
