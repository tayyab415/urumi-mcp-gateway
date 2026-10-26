#!/usr/bin/env python3
"""Check the Slack side of the Urumi setup before any Slack MCP code exists.

Read-only by default. It proves four things: the token works, it has the scopes
the Slack MCP server will need, the channel exists, and the bot is in it.

    python3 infra/slack/bootstrap.py              # check only
    python3 infra/slack/bootstrap.py --save       # also write SLACK_CHANNEL_ID into the env file
    python3 infra/slack/bootstrap.py --post-test  # also post one line to the channel

Secrets are read from ~/.urumi/slack.env (override with --env) and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = os.environ.get("SLACK_API_BASE", "https://slack.com/api")
DEFAULT_ENV = Path.home() / ".urumi" / "slack.env"
# Keep in step with manifest.json. Changing scopes later means reinstalling the app.
REQUIRED_SCOPES = {
    "chat:write",
    "chat:write.customize",
    "channels:read",
    "channels:history",
    "channels:join",
    "users:read",
}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def call(token: str, method: str, **params: str) -> tuple[dict, set[str]]:
    request = urllib.request.Request(
        f"{API}/{method}",
        data=urllib.parse.urlencode(params).encode(),
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.load(response)
            scopes = {s.strip() for s in response.headers.get("x-oauth-scopes", "").split(",") if s.strip()}
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"Could not reach Slack ({exc}).") from exc
    return body, scopes


def find_channel(token: str, name: str) -> dict | None:
    cursor = ""
    while True:
        body, _ = call(token, "conversations.list", types="public_channel", exclude_archived="true", limit="200", cursor=cursor)
        if not body.get("ok"):
            raise SystemExit(f"conversations.list failed: {body.get('error')}")
        for channel in body["channels"]:
            if channel["name"] == name:
                return channel
        cursor = body.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--save", action="store_true", help="write SLACK_CHANNEL_ID into the env file")
    parser.add_argument("--post-test", action="store_true", help="post one test line to the channel")
    args = parser.parse_args()

    if not args.env.exists():
        raise SystemExit(f"{args.env} not found. Copy infra/slack/slack.env.example there and fill in the token.")
    env = read_env(args.env)
    token = env.get("SLACK_BOT_TOKEN", "")
    channel_name = env.get("SLACK_CHANNEL_NAME", "urumi-restock")
    if not token.startswith("xoxb-"):
        raise SystemExit("SLACK_BOT_TOKEN must be a bot token that starts with xoxb-.")

    ok = True
    who, scopes = call(token, "auth.test")
    if not who.get("ok"):
        raise SystemExit(f"Slack rejected the token: {who.get('error')}")
    print(f"token     ok     workspace {who['team']} ({who['url']}), bot user '{who['user']}'")

    missing = sorted(REQUIRED_SCOPES - scopes)
    if missing:
        ok = False
        print(f"scopes    MISSING {', '.join(missing)}")
        print("          Create the app from infra/slack/manifest.json, or add these scopes and reinstall it.")
    else:
        print("scopes    ok     all six present")

    channel = find_channel(token, channel_name)
    if channel is None:
        print(f"channel   MISSING no public channel named #{channel_name}. Create it in Slack, then run this again.")
        return 1
    print(f"channel   ok     #{channel_name} is {channel['id']}")

    if not channel.get("is_member"):
        joined, _ = call(token, "conversations.join", channel=channel["id"])
        if joined.get("ok"):
            print("member    ok     the bot joined the channel just now")
        else:
            ok = False
            print(f"member    FAILED {joined.get('error')}. Invite the bot by hand: /invite @urumi")
    else:
        print("member    ok     the bot is already in the channel")

    if args.save and ok and "SLACK_CHANNEL_ID" not in env:
        with args.env.open("a") as handle:
            handle.write(f"SLACK_CHANNEL_ID={channel['id']}\n")
        print(f"saved     ok     SLACK_CHANNEL_ID added to {args.env}")

    if args.post_test and ok:
        posted, _ = call(token, "chat.postMessage", channel=channel["id"], text="Urumi setup check: the gateway's Slack bot can post here.")
        print("post      ok     one line posted" if posted.get("ok") else f"post      FAILED {posted.get('error')}")
        ok = ok and bool(posted.get("ok"))

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
