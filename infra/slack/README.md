# Slack setup for the Urumi gateway

Picture a notice board in an office. The gateway holds the only pen. Alice, Bob and the rest never touch Slack. They ask the gateway to write, the gateway checks their role, and then it writes with its one pen. The pen is a Slack **bot token**.

This folder sets up the board and the pen before any Slack MCP code exists.

## What is already on this machine

The Slack workspace `1horizon` (https://1horizon-workspace.slack.com) exists. It has two people and four bots: `cursor`, `careplus`, `careplus2` and `counter`. The Counter and careplus projects keep their tokens in `~/.counter-slack.env` and `~/workspaces/careplus/kept/slack-runtime.env`.

Urumi gets its own bot in the same workspace. Reusing Counter's token would not work, and it would be the wrong move anyway:

- `bootstrap.py` shows Counter's token lacks `channels:history` and `chat:write.customize`.
- One token shared by two projects means revoking it for one breaks the other.

## The four users and the admin

They live in the gateway, not in Slack. The gateway has its own users, roles and keys. Slack only ever sees the bot. The audit log is what records which person asked for each post.

Slack messages also carry the person's name. The Slack server on the gateway's list has "Tell this server who is asking" switched on (`forward_identity`). The gateway then sends the caller's display name, never the email, in an `X-Urumi-User` header, and `post_message` uses the manifest's `chat:write.customize` scope to post as `Bob (via urumi)`. Only the gateway can reach the Slack server, so the header is the gateway's word, and the server still cleans it (control characters, length). Other servers get nothing unless an admin switches it on for them. Checked on real Slack: Bob's post read back as `Bob (via urumi)`, the admin's as `Admin (via urumi)`. By default only admins can post, so most posts show the admin's name; an admin can open `post_message` to members on the Permissions page.

## What exists now (set up on 2026-10-25)

| Piece | Value |
| --- | --- |
| Slack app | "Urumi Gateway", app ID `A0CBK1NPZU7`, bot user `urumi`, installed in `1horizon` |
| Channel | `#urumi-restock`, `C0CBU62S26P`. Counter's bot created it, because the Slack CLI login cannot create channels. The Urumi bot joined it |
| Bot token | `~/.urumi/slack.env` (mode 600), with the channel ID and app ID. Never in the repo |
| CLI project | `~/.urumi/slack-cli/`, a stub that points the Slack CLI at `manifest.json` here |

`python3 infra/slack/bootstrap.py` passes against the real workspace: token, all six scopes, channel, membership, and one test post.

## How it was made, and how to redo it

The Slack CLI is logged in to `1horizon` on this machine (`slack auth list`). The same login created Counter's app.

```bash
cd ~/.urumi/slack-cli
slack manifest validate --team T0C1KL7LCFK                    # checks manifest.json
slack app install --team T0C1KL7LCFK --environment deployed   # creates and installs the app
```

The CLI does not print the bot token. The token comes from Slack's `apps.developerInstall` call with the CLI's own login token, and goes straight into `~/.urumi/slack.env`. If the manifest changes (for example a new scope), run `slack app install` again, then fetch the token again.

To start over: `slack app delete` in `~/.urumi/slack-cli`, archive the channel, delete `~/.urumi/slack.env`.

## In the cluster

Done on 2026-10-25: `charts/urumi-app` has an optional `slack-mcp` workload (`slack.enabled`), its Secret keys
(`slack-bot-token`, `slack-mcp-token`), a NetworkPolicy that lets only the gateway in and only public HTTPS out,
and the `gcp-vm.sh build-images` entry. The steps to turn it on are at the end of `charts/urumi-app/README.md`.

## Why we write the Slack MCP server ourselves

The popular open-source one, korotovsky/slack-mcp-server, documents the `stdio` and `sse` transports. The gateway needs Streamable HTTP, and that server's README does not list it. Our own server is about 100 lines: `list_channels`, `read_messages` and `post_message`, restricted to the one channel. That also keeps it simple to give `post_message` a "changes data" mark, so the per-role rules apply to it.
