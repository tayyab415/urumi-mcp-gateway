"""HTTP API for the dashboard chat.

POST /api/chat
    Header: Authorization: Bearer <the signed-in user's gateway credential>
    Body:   {"message": "...", "conversation_id": "..." | null}
    Reply:  text/event-stream, one JSON event per `data:` line.

POST /api/chat/approve
    Header: the same bearer credential
    Body:   {"conversation_id": "...", "request_id": "...", "decision": "approve" | "deny"}
    Answers an `approval_required` event. The held tool call then runs, or
    is refused. Only the conversation's owner can answer.

GET /api/chat/meta
    The model behind the chat and the approval timeout, for the dashboard header.

The chat service does not check the credential itself. It passes it to the
gateway, and the gateway accepts or rejects it. A bad credential fails at the
gateway's tool listing, before any model call is paid for.

The turn runs as its own task. If the browser disconnects halfway, the turn
still finishes, so the stored conversation never ends on a tool call that
has no recorded result.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route

from .agent import ChatAgent, model_label
from .approvals import ApprovalRegistry
from .config import Settings
from .conversations import ConversationStore, owner_of
from .model import BedrockModels

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
for noisy in ("httpx2", "httpx", "openai.agents"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("chat_agent")

settings = Settings.from_env()
store = ConversationStore(settings.conversation_ttl_seconds)
approvals = ApprovalRegistry()
_turns: set[asyncio.Task[None]] = set()


@asynccontextmanager
async def lifespan(app: Starlette):
    models = BedrockModels(settings)
    app.state.agent = ChatAgent(models, settings, approvals=approvals)
    log.info("chat agent ready: model=%s gateway=%s", settings.model, settings.gateway_url)
    yield
    await models.close()


async def chat(request: Request) -> JSONResponse | StreamingResponse:
    credential = _bearer(request)
    if credential is None:
        return _error(401, "Sign in first: the request has no bearer credential.")
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "The body must be JSON.")
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, str) or not message.strip():
        return _error(400, "Send a non-empty `message`.")
    if len(message) > settings.max_message_chars:
        return _error(400, f"Messages are limited to {settings.max_message_chars} characters.")
    conversation_id = body.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, str):
        return _error(400, "`conversation_id` must be a string or null.")

    conversation = store.get_or_create(conversation_id, credential)
    if conversation is None:
        return _error(404, "Conversation not found. Start a new one.")
    if conversation.busy:
        return _error(409, "This conversation is still answering the previous message.")
    if conversation.turns >= settings.max_turns_per_conversation:
        return _error(409, "This conversation is full. Start a new one.")
    conversation.busy = True
    conversation.turns += 1

    agent: ChatAgent = request.app.state.agent
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def run_turn() -> None:
        try:
            await agent.run_turn(
                conversation.messages, message.strip(), credential, queue.put_nowait,
                conversation_id=conversation.id, owner=conversation.owner,
            )
        except Exception:
            log.exception("chat turn failed")
            queue.put_nowait({"type": "error", "message": "The chat service hit an unexpected error."})
        finally:
            conversation.busy = False
            store.keep(conversation)
            queue.put_nowait(None)

    task = asyncio.create_task(run_turn())
    _turns.add(task)
    task.add_done_callback(_turns.discard)

    async def events():
        yield _sse({"type": "conversation", "id": conversation.id, "model": settings.model, "model_label": agent.model_label})
        while (event := await queue.get()) is not None:
            yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def approve(request: Request) -> JSONResponse:
    credential = _bearer(request)
    if credential is None:
        return _error(401, "Sign in first: the request has no bearer credential.")
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "The body must be JSON.")
    if not isinstance(body, dict):
        return _error(400, "The body must be a JSON object.")
    conversation_id, request_id, decision = body.get("conversation_id"), body.get("request_id"), body.get("decision")
    if not isinstance(conversation_id, str) or not isinstance(request_id, str) or decision not in ("approve", "deny"):
        return _error(400, "Send `conversation_id`, `request_id` and a `decision` of approve or deny.")
    agent: ChatAgent = request.app.state.agent
    registry = agent.approvals if agent.approvals is not None else approvals
    # The owner check is the credential's hash, the same one that owns the conversation.
    outcome = registry.decide(request_id, conversation_id, owner_of(credential), decision)
    if outcome == "not_found":
        return _error(404, "No call is waiting for that decision.")
    if outcome == "already_decided":
        return _error(409, "That call was already decided.")
    return JSONResponse({"ok": True})


async def meta(request: Request) -> JSONResponse:
    return JSONResponse({"model": settings.model, "model_label": model_label(settings.model),
                         "approval_timeout_seconds": settings.approval_timeout_seconds})


async def healthz(request: Request) -> PlainTextResponse:
    # Liveness and readiness both mean "this process can serve requests".
    # Neither checks the gateway or Bedrock: an outage there should show up as
    # an error in chat, not as Kubernetes restarting healthy chat Pods.
    return PlainTextResponse("ok")


def _bearer(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    value = value.strip()
    return value if scheme.lower() == "bearer" and value else None


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


app = Starlette(
    routes=[
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/chat/approve", approve, methods=["POST"]),
        Route("/api/chat/meta", meta, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
    ],
    lifespan=lifespan,
)
