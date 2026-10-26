"""The approval gate: a tool that can change data waits for the user.

The model asks for `woo__update_order_status`. Before the call reaches the
gateway, chat sends the dashboard an `approval_required` event and parks the
call on a Future. The user's click arrives as `POST /api/chat/approve`, which
resolves that Future. Only then does the gateway see the call. A denial, or
no answer in time, goes back to the model as a tool result that says the call
did not run.

The hold is real: nothing about it lives only in the UI. The gateway call is
not made until the Future resolves with "approve".

Two objects:

- `ApprovalRegistry` is process-wide. It maps a request id to its pending
  Future and remembers which conversation (and so which credential) owns it,
  so one user cannot approve another user's call.
- `ApprovalGate` is per turn. It knows how to emit events for that turn and
  how long to wait.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Decision = Literal["approve", "deny", "timeout"]
DecideResult = Literal["ok", "not_found", "already_decided"]

DENIED_TEXT = "The user denied this call. It did not run."
TIMED_OUT_TEXT = "No decision was made in time. The call did not run."


@dataclass
class PendingApproval:
    request_id: str
    conversation_id: str
    owner: str  # hash of the credential that owns the conversation
    name: str
    future: asyncio.Future[Decision] = field(default_factory=lambda: asyncio.get_event_loop().create_future())


class ApprovalRegistry:
    """Every call waiting for a decision, across all conversations."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}

    def open(self, conversation_id: str, owner: str, name: str) -> PendingApproval:
        pending = PendingApproval(request_id=secrets.token_urlsafe(12), conversation_id=conversation_id, owner=owner, name=name)
        self._pending[pending.request_id] = pending
        return pending

    def decide(self, request_id: str, conversation_id: str, owner: str, decision: Literal["approve", "deny"]) -> DecideResult:
        """Resolve a pending call. An unknown id and someone else's id look the same."""
        pending = self._pending.get(request_id)
        if pending is None or pending.conversation_id != conversation_id or not hmac.compare_digest(pending.owner, owner):
            return "not_found"
        if pending.future.done():
            return "already_decided"
        pending.future.set_result(decision)
        return "ok"

    def drop(self, request_id: str) -> None:
        self._pending.pop(request_id, None)

    def pending(self, conversation_id: str | None = None) -> list[PendingApproval]:
        return [p for p in self._pending.values() if conversation_id is None or p.conversation_id == conversation_id]


@dataclass
class Verdict:
    decision: Decision
    waited_ms: int

    @property
    def approved(self) -> bool:
        return self.decision == "approve"

    @property
    def refusal(self) -> str:
        return TIMED_OUT_TEXT if self.decision == "timeout" else DENIED_TEXT


class ApprovalGate:
    """One turn's view of the registry: emits the events and waits for the answer."""

    def __init__(
        self,
        registry: ApprovalRegistry,
        conversation_id: str,
        owner: str,
        emit: Callable[[dict[str, Any]], None],
        timeout_seconds: float,
    ):
        self._registry = registry
        self._conversation_id = conversation_id
        self._owner = owner
        self._emit = emit
        self._timeout = timeout_seconds

    async def ask(self, *, call_id: str, name: str, arguments: dict[str, Any], summary: str, server: str, server_name: str = "") -> Verdict:
        pending = self._registry.open(self._conversation_id, self._owner, name)
        self._emit(
            {
                "type": "approval_required",
                "request_id": pending.request_id,
                "call_id": call_id,
                "name": name,
                "input": arguments,
                "summary": summary,
                "server": server,  # the slug, e.g. store-north
                "server_name": server_name or server,  # what the person reads, e.g. "Store: North"
                "read_only": False,
            }
        )
        started = time.monotonic()
        try:
            decision: Decision = await asyncio.wait_for(asyncio.shield(pending.future), self._timeout)
        except TimeoutError:
            decision = "timeout"
        finally:
            self._registry.drop(pending.request_id)
        self._emit({"type": "approval_decided", "request_id": pending.request_id, "decision": decision})
        return Verdict(decision=decision, waited_ms=int((time.monotonic() - started) * 1000))
