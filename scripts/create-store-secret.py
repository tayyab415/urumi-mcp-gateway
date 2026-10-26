#!/usr/bin/env python3
"""Prepare a private Kubernetes Secret file without contacting a cluster."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets


ROOT = Path(__file__).resolve().parent.parent
# WooCommerce REST key for woo-mcp. WooCommerce keeps only a hash of the consumer
# key, so we choose both values here and the setup Job registers them.
GENERATORS = {
    "mariadb-root-password": lambda: secrets.token_urlsafe(32),
    "wordpress-db-password": lambda: secrets.token_urlsafe(32),
    "wordpress-admin-password": lambda: secrets.token_urlsafe(32),
    "woo-consumer-key": lambda: "ck_" + secrets.token_hex(20),
    "woo-consumer-secret": lambda: "cs_" + secrets.token_hex(20),
}
KEYS = tuple(GENERATORS)


def kubernetes_name(value: str) -> str:
    if len(value) > 63 or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", value):
        raise argparse.ArgumentTypeError("Use a lowercase Kubernetes name, at most 63 characters.")
    return value


def write_private(path: Path, document: dict, replace: bool) -> None:
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(document, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        os.replace(temporary, path)
    else:
        # Fails if another run created the file in the meantime.
        os.link(temporary, path)
        os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", type=kubernetes_name, default="urumi-store")
    parser.add_argument("--name", type=kubernetes_name, default="urumi-store-credentials")
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".runtime/store/credentials.json"
    )
    parser.add_argument(
        "--rotate-admin-password",
        action="store_true",
        help="Replace the WordPress admin password. The setup Job applies it on the next helm upgrade.",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    if args.output.exists():
        # Database passwords are already in use by MariaDB; never rotate them here.
        document = json.loads(args.output.read_text())
        changed = [key for key in KEYS if key not in document["stringData"]]
        if args.rotate_admin_password:
            changed.append("wordpress-admin-password")
        if not changed:
            parser.exit(0, "Secret file already has every key; kept it unchanged.\n")
        for key in dict.fromkeys(changed):
            document["stringData"][key] = GENERATORS[key]()
        write_private(args.output, document, replace=True)
        print(f"Set {', '.join(dict.fromkeys(changed))} in {args.output}; other keys unchanged.")
    else:
        document = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": args.name, "namespace": args.namespace},
            "type": "Opaque",
            "stringData": {key: make() for key, make in GENERATORS.items()},
        }
        write_private(args.output, document, replace=False)
        print(f"Prepared private credentials file: {args.output}")
    print("No secrets were displayed or sent to Kubernetes.")


if __name__ == "__main__":
    main()
