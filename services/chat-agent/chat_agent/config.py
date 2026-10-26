"""Settings, read once from environment variables.

The Bedrock credential is not a setting here. `model.py` uses
`AWS_BEARER_TOKEN_BEDROCK` (a Bedrock API key) if it is set, and otherwise
signs short-lived keys from the normal AWS credential chain. In Kubernetes
either one comes from a Secret, like every other credential in the system.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # The gateway's one MCP endpoint. Chat reaches tools only through it.
    gateway_url: str
    # Kimi K3 through Bedrock's US cross-region inference profile.
    model: str = "us.moonshotai.kimi-k3"
    aws_region: str = "us-east-1"
    # Chat is a light workload. "low" keeps answers fast and cheap.
    effort: str = "low"
    # Model calls allowed in one user turn before the loop stops.
    max_steps: int = 8
    # How long chat waits for one tool call. Keep it longer than the gateway's
    # own upstream timeout, so the gateway's clearer error arrives first.
    tool_timeout_seconds: float = 45.0
    # How long a tool that changes data waits for the user's Approve or Deny
    # before it counts as denied.
    approval_timeout_seconds: float = 300.0
    max_message_chars: int = 4000
    max_turns_per_conversation: int = 30
    conversation_ttl_seconds: int = 3600

    @property
    def bedrock_openai_url(self) -> str:
        # Bedrock Runtime's OpenAI-compatible endpoint. It serves the
        # Responses API, which is what the Agents SDK speaks natively.
        return f"https://bedrock-runtime.{self.aws_region}.amazonaws.com/openai/v1"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            gateway_url=env.get("GATEWAY_MCP_URL", "http://127.0.0.1:8100/mcp"),
            model=env.get("CHAT_MODEL", cls.model),
            aws_region=env.get("AWS_REGION", cls.aws_region),
            effort=env.get("CHAT_EFFORT", cls.effort),
            max_steps=int(env.get("CHAT_MAX_STEPS", cls.max_steps)),
            tool_timeout_seconds=float(env.get("CHAT_TOOL_TIMEOUT_SECONDS", cls.tool_timeout_seconds)),
            approval_timeout_seconds=float(env.get("CHAT_APPROVAL_TIMEOUT_SECONDS", cls.approval_timeout_seconds)),
            max_message_chars=int(env.get("CHAT_MAX_MESSAGE_CHARS", cls.max_message_chars)),
            max_turns_per_conversation=int(env.get("CHAT_MAX_TURNS", cls.max_turns_per_conversation)),
            conversation_ttl_seconds=int(env.get("CHAT_CONVERSATION_TTL_SECONDS", cls.conversation_ttl_seconds)),
        )
