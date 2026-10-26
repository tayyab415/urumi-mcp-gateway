#!/usr/bin/env python3
"""Prepare the app's private Kubernetes Secret file without contacting a cluster.

Random values are made once and then kept. The WooCommerce REST key is copied
from the store's Secret file on every run, because the store and woo-mcp must
hold the same key.
"""

import argparse
import json
import os
from pathlib import Path
import secrets


ROOT = Path(__file__).resolve().parent.parent
GENERATED = {
    # Seals upstream credentials in the gateway's database. Changing it makes
    # them unreadable, so this script never replaces it.
    "gateway-secret-key": lambda: secrets.token_urlsafe(48),
    # The first admin's password. The gateway uses it once, on its first start.
    "admin-password": lambda: secrets.token_urlsafe(18),
    "shipping-api-token": lambda: secrets.token_urlsafe(32),
    "supplier-api-key": lambda: secrets.token_urlsafe(32),
    # What the gateway sends to slack-mcp. Unused until `slack.enabled` is set in the chart.
    "slack-mcp-token": lambda: secrets.token_urlsafe(32),
    # What the gateway sends to the store provisioner (the multi-store bonus).
    "provisioner-token": lambda: secrets.token_urlsafe(32),
}
COPIED_FROM_STORE = ("woo-consumer-key", "woo-consumer-secret")
# The Slack bot token lives in a private file outside the repo (infra/slack/README.md).
# It is copied only if that file exists, so a machine without Slack still works.
SLACK_ENV = Path.home() / ".urumi" / "slack.env"


def write_private(path: Path, document: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(document, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="urumi-app")
    parser.add_argument("--name", default="urumi-app-secrets")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/app/secrets.json")
    parser.add_argument("--store", type=Path, default=ROOT / ".runtime/store/credentials.json")
    args = parser.parse_args()

    store = json.loads(args.store.read_text())["stringData"]
    missing = [key for key in COPIED_FROM_STORE if key not in store]
    if missing:
        parser.error(f"{args.store} has no {', '.join(missing)}. Run scripts/create-store-secret.py first.")

    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.output.exists():
        document = json.loads(args.output.read_text())
    else:
        document = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": args.name, "namespace": args.namespace},
            "type": "Opaque",
            "stringData": {},
        }
    data = document["stringData"]
    changed = [key for key in GENERATED if key not in data]
    for key in changed:
        data[key] = GENERATED[key]()
    for key in COPIED_FROM_STORE:
        if data.get(key) != store[key]:
            data[key] = store[key]
            changed.append(key)
    if SLACK_ENV.exists():
        env = dict(line.split("=", 1) for line in SLACK_ENV.read_text().splitlines() if "=" in line and not line.startswith("#"))
        token = env.get("SLACK_BOT_TOKEN", "").strip()
        if token.startswith("xoxb-") and data.get("slack-bot-token") != token:
            data["slack-bot-token"] = token
            changed.append("slack-bot-token")
    if not changed:
        parser.exit(0, "Secret file already up to date; kept it unchanged.\n")
    write_private(args.output, document)
    print(f"Set {', '.join(changed)} in {args.output}; other keys unchanged.")
    print("No secrets were displayed or sent to Kubernetes.")


if __name__ == "__main__":
    main()
