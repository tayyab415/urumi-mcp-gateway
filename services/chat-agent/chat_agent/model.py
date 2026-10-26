"""The model: Kimi K3 on Amazon Bedrock, seen by the Agents SDK as an OpenAI model.

Bedrock Runtime has an OpenAI-compatible endpoint that serves the Responses
API. The Agents SDK's own `OpenAIResponsesModel` talks to it unchanged. The
only differences from calling OpenAI are the base URL and the key.

The key is a Bedrock API key. If `AWS_BEARER_TOKEN_BEDROCK` is set, it is used
as is. Otherwise a short-lived key is signed locally from the AWS credentials
the process already has (nothing is created in the AWS account) and replaced
every 30 minutes, well inside its 12-hour life.
"""

from __future__ import annotations

import os
import time

from agents import OpenAIResponsesModel
from aws_bedrock_token_generator import provide_token
from openai import AsyncOpenAI

from .config import Settings

_REFRESH_SECONDS = 30 * 60


class BedrockModels:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._fixed_key = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        self._client: AsyncOpenAI | None = None
        self._previous: AsyncOpenAI | None = None
        self._made_at = 0.0

    async def model(self) -> OpenAIResponsesModel:
        stale = not self._fixed_key and time.monotonic() - self._made_at > _REFRESH_SECONDS
        if self._client is None or stale:
            # A turn may still be using the current client, so it retires for one
            # refresh period before it is closed. No turn lasts 30 minutes.
            if self._previous is not None:
                await self._previous.close()
            self._previous = self._client
            key = self._fixed_key or provide_token(region=self._settings.aws_region)
            self._client = AsyncOpenAI(base_url=self._settings.bedrock_openai_url, api_key=key)
            self._made_at = time.monotonic()
        return OpenAIResponsesModel(model=self._settings.model, openai_client=self._client)

    async def close(self) -> None:
        for client in (self._previous, self._client):
            if client is not None:
                await client.close()
