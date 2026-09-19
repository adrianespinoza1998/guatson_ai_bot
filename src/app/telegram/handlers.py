"""Shared message-processing logic, used by both the webhook route
(app.telegram.webhook) and the polling entrypoint (scripts/run_polling.py).

`handle_incoming()` is transport-agnostic: allow-list check, idempotent insert, and
either enqueueing or awaiting the (slow) agent turn. The webhook route calls it
directly so it keeps full control of when the HTTP response is sent (passing a
`Queue` so the agent turn runs after the response); the Router below wires the same
function into aiogram for polling mode, where there's no HTTP deadline to respect.
"""

from __future__ import annotations

import datetime as dt

from aiogram import Bot, Router
from aiogram.types import Message as TgMessage
from aiogram.types import Update

from app.config import Settings
from app.db import session_scope
from app.llm.agent import AgentResult, run_turn
from app.llm.client import ClaudeClient
from app.logging import get_logger
from app.models import Message as DbMessage
from app.queue import Queue
from app.repositories.messages import insert_user_message
from app.telegram.sender import send_artifact_document, send_text, send_typing

logger = get_logger(__name__)

router = Router(name="guatson")

UNSUPPORTED_CONTENT_REPLY = (
    "Por ahora solo puedo leer mensajes de texto. Todavía no soporto voz, imágenes ni documentos."
)
AGENT_LIMIT_REPLY = (
    "Llegué al límite de pasos para esta solicitud sin terminar. "
    "Intenta de nuevo con un pedido más simple."
)
PROCESSING_ERROR_REPLY = "Tuve un problema procesando tu mensaje. Intenta de nuevo en un momento."


def is_allowed(user_id: int | None, settings: Settings) -> bool:
    return user_id is not None and user_id in settings.telegram_allowed_user_ids


async def ingest_incoming(
    *, chat_id: int, telegram_update_id: int, message: TgMessage
) -> tuple[DbMessage | None, bool]:
    """Idempotently insert the incoming message. Returns (row, is_text):
    - row is None if telegram_update_id was already stored (a retried delivery) — the
      caller must treat that as a silent no-op, never as an error.
    - is_text is False for voice/photo/document/etc — out of scope for the MVP.
    """
    is_text = message.text is not None
    if is_text:
        content = [{"type": "text", "text": message.text}]
        text_preview = (message.text or "")[:2000]
    else:
        content = [{"type": "text", "text": f"[mensaje no soportado: {message.content_type}]"}]
        text_preview = None

    async with session_scope() as session:
        inserted = await insert_user_message(
            session,
            chat_id=chat_id,
            telegram_update_id=telegram_update_id,
            content=content,
            text_preview=text_preview,
        )
    return inserted, is_text


def _reply_text_for(result: AgentResult) -> str:
    texts = [
        text_value
        for block in result.final_message.content
        if block.get("type") == "text" and isinstance(text_value := block.get("text"), str)
    ]
    text = "\n".join(texts).strip()
    if text:
        return text
    if result.hit_iteration_limit:
        return AGENT_LIMIT_REPLY
    return "Listo."


async def process_message(
    *, bot: Bot, chat_id: int, settings: Settings, client: ClaudeClient
) -> None:
    """The slow part: run the agent loop, then send the reply and any newly-saved
    artifacts. Any failure is logged and turned into a short Spanish error message —
    a mid-turn failure rolls the DB transaction back, so it never leaves a `tool_use`
    without its `tool_result` (see app.llm.agent).
    """
    await send_typing(bot, chat_id)

    try:
        async with session_scope() as session:
            result = await run_turn(
                session=session,
                settings=settings,
                client=client,
                chat_id=chat_id,
                now=dt.datetime.now(dt.UTC),
            )
            reply_text = _reply_text_for(result)
            saved_artifacts = result.saved_artifacts
    except Exception:
        logger.exception("agent_turn_failed", chat_id=chat_id)
        await send_text(bot, chat_id, PROCESSING_ERROR_REPLY)
        return

    await send_text(bot, chat_id, reply_text)
    for artifact in saved_artifacts:
        await send_artifact_document(
            bot,
            chat_id,
            name=artifact["name"],
            language=artifact["language"],
            code=artifact["code"],
        )


async def handle_incoming(
    *,
    bot: Bot,
    message: TgMessage,
    telegram_update_id: int,
    settings: Settings,
    client: ClaudeClient,
    queue: Queue | None = None,
) -> None:
    """Steps 2-4 (and, inline or via `queue`, step 5) of SPEC.md section 6. Pass a
    `Queue` to defer the agent turn (webhook mode); omit it to run inline (polling).
    """
    if not is_allowed(message.from_user.id if message.from_user else None, settings):
        return

    inserted, is_text = await ingest_incoming(
        chat_id=message.chat.id, telegram_update_id=telegram_update_id, message=message
    )
    if inserted is None:
        return

    if not is_text:
        await send_text(bot, message.chat.id, UNSUPPORTED_CONTENT_REPLY)
        return

    coro = process_message(bot=bot, chat_id=message.chat.id, settings=settings, client=client)
    if queue is not None:
        queue.enqueue(coro)
    else:
        await coro


@router.message()
async def on_message(
    message: TgMessage, event_update: Update, bot: Bot, settings: Settings, client: ClaudeClient
) -> None:
    await handle_incoming(
        bot=bot,
        message=message,
        telegram_update_id=event_update.update_id,
        settings=settings,
        client=client,
    )
