"""Conversations, kept in memory.

A conversation belongs to the credential that started it. Another user who
guesses its ID gets the same "not found" as for an ID that never existed.

Memory is enough for a basic dashboard chat: a restarted Pod starts with no
conversations, and the user starts a new one. The audit log, which matters,
lives in the gateway.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


def owner_of(credential: str) -> str:
    """The stored name of a credential: its hash, never the credential itself."""
    return hashlib.sha256(credential.encode()).hexdigest()


@dataclass
class Conversation:
    id: str
    owner: str
    messages: list[Any] = field(default_factory=list)
    turns: int = 0
    busy: bool = False
    last_used: float = field(default_factory=time.monotonic)


class ConversationStore:
    def __init__(self, ttl_seconds: int, max_conversations: int = 1000):
        self._ttl = ttl_seconds
        self._max = max_conversations
        self._items: dict[str, Conversation] = {}

    def get_or_create(self, conversation_id: str | None, credential: str) -> Conversation | None:
        """Find the caller's conversation, or start a new one (not stored yet)."""
        self._expire()
        owner = owner_of(credential)
        if conversation_id is None:
            return Conversation(id=secrets.token_urlsafe(16), owner=owner)
        conversation = self._items.get(conversation_id)
        if conversation is None or not hmac.compare_digest(conversation.owner, owner):
            return None
        conversation.last_used = time.monotonic()
        return conversation

    def keep(self, conversation: Conversation) -> None:
        """Store a conversation once the gateway has accepted a turn of it.

        A request with a bad credential never gets this far, so strangers
        can't fill the store and push out real users' conversations.
        """
        if conversation.messages:
            conversation.last_used = time.monotonic()
            self._items[conversation.id] = conversation

    def _expire(self) -> None:
        now = time.monotonic()
        idle = [c for c in self._items.values() if not c.busy]
        for c in idle:
            if now - c.last_used > self._ttl:
                del self._items[c.id]
        # Over the cap: drop the least recently used idle conversations.
        excess = len(self._items) - self._max + 1
        if excess > 0:
            for c in sorted((c for c in self._items.values() if not c.busy), key=lambda c: c.last_used)[:excess]:
                del self._items[c.id]
