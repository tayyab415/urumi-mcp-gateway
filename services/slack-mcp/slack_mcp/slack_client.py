"""Small async client for the three Slack Web API calls this server needs.

Only this module talks to Slack. The MCP tools in server.py call it and never
see the bot token. The client is tied to one channel: no method takes a channel
argument, so no tool can be talked into reading or posting somewhere else.
"""

from __future__ import annotations

from typing import Any

import httpx2


class SlackError(Exception):
    """A plain-language failure the model and the user can act on."""


# What Slack's short error codes mean for the person reading the result.
EXPLAIN = {
    "not_in_channel": "The Slack bot is not in the channel. An admin must invite it (/invite @urumi).",
    "channel_not_found": "The configured Slack channel does not exist or the bot cannot see it.",
    "is_archived": "The Slack channel is archived.",
    "invalid_auth": "Slack rejected the bot token.",
    "token_revoked": "Slack rejected the bot token: it was revoked.",
    "account_inactive": "Slack rejected the bot token: the app was removed from the workspace.",
    "msg_too_long": "Slack refused the message because it is too long.",
    "no_text": "Slack refused an empty message.",
    "restricted_action": "Workspace settings stop the bot from posting in this channel.",
}


class SlackClient:
    def __init__(
        self,
        token: str,
        channel_id: str,
        *,
        api_base: str = "https://slack.com/api",
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self.channel_id = channel_id
        self._http = httpx2.AsyncClient(
            base_url=api_base,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=timeout,
        )
        self._names: dict[str, str] = {}

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        try:
            response = await self._http.post(f"/{method}", data={k: v for k, v in params.items() if v is not None})
        except httpx2.TimeoutException:
            raise SlackError("Slack did not answer in time.") from None
        except httpx2.HTTPError as exc:
            raise SlackError(f"Could not reach Slack ({exc.__class__.__name__}).") from None
        if response.status_code == 429:
            wait = response.headers.get("Retry-After", "a few")
            raise SlackError(f"Slack is rate limiting this app. Try again in {wait} seconds.")
        try:
            body = response.json()
        except ValueError:
            raise SlackError(f"Slack answered with something that is not JSON (HTTP {response.status_code}).") from None
        if not body.get("ok"):
            code = str(body.get("error", "unknown_error"))
            if code == "missing_scope":
                raise SlackError(f"The Slack app lacks the permission {body.get('needed', 'it needs')}. Add it and reinstall the app.")
            raise SlackError(EXPLAIN.get(code, f"Slack refused: {code}."))
        return body

    async def channel(self) -> dict[str, Any]:
        return (await self.call("conversations.info", channel=self.channel_id, include_num_members="true"))["channel"]

    async def history(self, limit: int) -> list[dict[str, Any]]:
        return (await self.call("conversations.history", channel=self.channel_id, limit=str(limit)))["messages"]

    async def post(self, text: str, thread_ts: str | None = None, username: str | None = None) -> dict[str, Any]:
        # `username` only changes the name Slack shows (the bot token's chat:write.customize scope);
        # the message is still posted by the bot.
        return await self.call(
            "chat.postMessage", channel=self.channel_id, text=text, thread_ts=thread_ts, username=username,
            link_names="0", unfurl_links="false",
        )

    async def user_name(self, user_id: str) -> str:
        if user_id not in self._names:
            try:
                info = (await self.call("users.info", user=user_id))["user"]
                self._names[user_id] = info.get("real_name") or info.get("name") or user_id
            except SlackError:
                self._names[user_id] = user_id  # a name is nice to have, not worth failing the read
        return self._names[user_id]

    async def author(self, message: dict[str, Any]) -> str:
        """A human-readable sender: a person's name, or the bot's name."""
        if message.get("user"):
            return await self.user_name(message["user"])
        return message.get("username") or message.get("bot_profile", {}).get("name") or "a bot"
