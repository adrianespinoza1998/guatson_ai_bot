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
from dataclasses import dataclass

from aiogram import Bot, Router
from aiogram.types import Message as TgMessage
from aiogram.types import Update
from aiogram.types import Voice as TgVoice

from app.config import Settings
from app.db import session_scope
from app.llm.agent import AgentResult, run_turn
from app.llm.client import ClaudeClient
from app.logging import get_logger
from app.models import Message as DbMessage
from app.queue import Queue
from app.repositories.messages import insert_user_message
from app.telegram.sender import send_artifact_document, send_text, send_typing
from app.transcription import Transcriber

logger = get_logger(__name__)

router = Router(name="guatson")

UNSUPPORTED_CONTENT_REPLY = (
    "Por ahora solo puedo leer texto y notas de voz. Todavía no soporto imágenes ni documentos."
)
AGENT_LIMIT_REPLY = (
    "Llegué al límite de pasos para esta solicitud sin terminar. "
    "Intenta de nuevo con un pedido más simple."
)
PROCESSING_ERROR_REPLY = "Tuve un problema procesando tu mensaje. Intenta de nuevo en un momento."
VOICE_UNAVAILABLE_REPLY = "Todavía no puedo transcribir audio en este bot."
VOICE_TRANSCRIPTION_ERROR_REPLY = (
    "No pude transcribir esa nota de voz. Intenta de nuevo en un momento."
)


def is_allowed(user_id: int | None, settings: Settings) -> bool:
    return user_id is not None and user_id in settings.telegram_allowed_user_ids


@dataclass
class IngestOutcome:
    """What `ingest_incoming` did with an update.

    - `row` is the inserted message, or None if nothing was inserted (a retried
      `telegram_update_id`, or a voice note rejected before ever reaching the DB).
    - `should_process` tells the caller whether to run the agent turn.
    - `reply_text`, when set, is sent immediately and no further processing happens —
      used for the "unsupported"/voice-limit/voice-error short-circuits.
    """

    row: DbMessage | None
    should_process: bool
    reply_text: str | None = None


async def _ingest_text(*, chat_id: int, telegram_update_id: int, text: str) -> IngestOutcome:
    async with session_scope() as session:
        row = await insert_user_message(
            session,
            chat_id=chat_id,
            telegram_update_id=telegram_update_id,
            content=[{"type": "text", "text": text}],
            text_preview=text[:2000],
        )
    return IngestOutcome(row=row, should_process=row is not None)


async def _ingest_unsupported(
    *, chat_id: int, telegram_update_id: int, content_type: str
) -> IngestOutcome:
    async with session_scope() as session:
        row = await insert_user_message(
            session,
            chat_id=chat_id,
            telegram_update_id=telegram_update_id,
            content=[{"type": "text", "text": f"[mensaje no soportado: {content_type}]"}],
            text_preview=None,
        )
    reply = UNSUPPORTED_CONTENT_REPLY if row is not None else None
    return IngestOutcome(row=row, should_process=False, reply_text=reply)


async def _ingest_voice(
    *,
    bot: Bot,
    chat_id: int,
    telegram_update_id: int,
    voice: TgVoice,
    settings: Settings,
    transcriber: Transcriber | None,
) -> IngestOutcome:
    """See docs/specs/audio-transcription.md. Every rejection path here — limits, a
    missing key, a failed transcription — intentionally inserts nothing into
    `messages`: it's not a turn that ran and failed, it's a turn that never started.
    """
    if voice.duration > settings.voice_max_duration_seconds:
        reply = (
            f"Esa nota de voz dura más de {settings.voice_max_duration_seconds} segundos, "
            "no la puedo transcribir. Intenta con una más corta."
        )
        return IngestOutcome(row=None, should_process=False, reply_text=reply)

    if voice.file_size is not None and voice.file_size > settings.voice_max_file_bytes:
        reply = "Esa nota de voz pesa demasiado, no la puedo descargar. Intenta con una más corta."
        return IngestOutcome(row=None, should_process=False, reply_text=reply)

    if transcriber is None:
        return IngestOutcome(row=None, should_process=False, reply_text=VOICE_UNAVAILABLE_REPLY)

    try:
        await send_typing(bot, chat_id)
        buffer = await bot.download(voice)
        if buffer is None:
            raise RuntimeError("bot.download() returned no buffer for a voice note")
        transcribed_text = await transcriber.transcribe(buffer.read(), filename="voice.ogg")
    except Exception:
        logger.exception("voice_transcription_failed", chat_id=chat_id)
        return IngestOutcome(
            row=None, should_process=False, reply_text=VOICE_TRANSCRIPTION_ERROR_REPLY
        )

    content_text = f"[nota de voz transcrita, {voice.duration}s] {transcribed_text}"
    return await _ingest_text(
        chat_id=chat_id, telegram_update_id=telegram_update_id, text=content_text
    )


async def ingest_incoming(
    *,
    bot: Bot,
    chat_id: int,
    telegram_update_id: int,
    message: TgMessage,
    settings: Settings,
    transcriber: Transcriber | None,
) -> IngestOutcome:
    if message.text is not None:
        return await _ingest_text(
            chat_id=chat_id, telegram_update_id=telegram_update_id, text=message.text
        )

    if message.voice is not None:
        return await _ingest_voice(
            bot=bot,
            chat_id=chat_id,
            telegram_update_id=telegram_update_id,
            voice=message.voice,
            settings=settings,
            transcriber=transcriber,
        )

    return await _ingest_unsupported(
        chat_id=chat_id, telegram_update_id=telegram_update_id, content_type=message.content_type
    )


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
    try:
        await send_typing(bot, chat_id)

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

        await send_text(bot, chat_id, reply_text)
        for artifact in saved_artifacts:
            await send_artifact_document(
                bot,
                chat_id,
                name=artifact["name"],
                language=artifact["language"],
                code=artifact["code"],
            )
    except Exception:
        # Covers a failed agent turn as well as a Telegram-side send failure — either
        # way, log it and try once to tell the user. If even that fails (e.g. the bot
        # itself can't reach Telegram), there's nothing more to do here.
        logger.exception("process_message_failed", chat_id=chat_id)
        try:
            await send_text(bot, chat_id, PROCESSING_ERROR_REPLY)
        except Exception:
            logger.exception("error_reply_failed", chat_id=chat_id)


async def handle_incoming(
    *,
    bot: Bot,
    message: TgMessage,
    telegram_update_id: int,
    settings: Settings,
    client: ClaudeClient,
    transcriber: Transcriber | None = None,
    queue: Queue | None = None,
) -> None:
    """Steps 2-4 (and, inline or via `queue`, step 5) of SPEC.md section 6. Pass a
    `Queue` to defer the agent turn (webhook mode); omit it to run inline (polling).
    """
    if not is_allowed(message.from_user.id if message.from_user else None, settings):
        return

    outcome = await ingest_incoming(
        bot=bot,
        chat_id=message.chat.id,
        telegram_update_id=telegram_update_id,
        message=message,
        settings=settings,
        transcriber=transcriber,
    )

    if outcome.reply_text is not None:
        await send_text(bot, message.chat.id, outcome.reply_text)

    if not outcome.should_process:
        return

    coro = process_message(bot=bot, chat_id=message.chat.id, settings=settings, client=client)
    if queue is not None:
        queue.enqueue(coro)
    else:
        await coro


@router.message()
async def on_message(
    message: TgMessage,
    event_update: Update,
    bot: Bot,
    settings: Settings,
    client: ClaudeClient,
    transcriber: Transcriber | None,
) -> None:
    await handle_incoming(
        bot=bot,
        message=message,
        telegram_update_id=event_update.update_id,
        settings=settings,
        client=client,
        transcriber=transcriber,
    )
