"""Voice-note ingestion: limits, missing key, transcription success/failure.

See docs/specs/audio-transcription.md. No real OpenAI or Telegram call is ever made —
FakeVoiceBot overrides the Telegram-hitting methods, FakeTranscriber stands in for
app.transcription.Transcriber.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from aiogram import Bot
from aiogram.types import Update
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_session_factory
from app.models import Message as DbMessage
from app.telegram.handlers import (
    VOICE_TRANSCRIPTION_ERROR_REPLY,
    VOICE_UNAVAILABLE_REPLY,
    ingest_incoming,
)

pytestmark = pytest.mark.asyncio


class FakeVoiceBot(Bot):
    def __init__(self, *, audio_bytes: bytes = b"fake-ogg-audio") -> None:
        super().__init__(token="123456789:AAFakeTestTokenNotReal12345678901")
        self._audio_bytes = audio_bytes
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:  # type: ignore[override]
        self.sent_messages.append((chat_id, text))

    async def send_chat_action(self, chat_id: int, action: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None

    async def download(self, file: Any, *args: Any, **kwargs: Any) -> io.BytesIO:  # type: ignore[override]
        return io.BytesIO(self._audio_bytes)


class FakeTranscriber:
    def __init__(self, *, text: str | None = None, error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.calls = 0

    async def transcribe(self, audio_bytes: bytes, *, filename: str = "voice.ogg") -> str:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._text is not None
        return self._text


def _make_voice_update(
    *, update_id: int, user_id: int, chat_id: int, duration: int, file_size: int = 12_345
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "voice": {
                "file_id": "AwACfake",
                "file_unique_id": "uniqfake",
                "duration": duration,
                "mime_type": "audio/ogg",
                "file_size": file_size,
            },
        },
    }


async def _count_messages(chat_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(func.count()).select_from(DbMessage).where(DbMessage.chat_id == chat_id)
        )
        return result.scalar_one()


async def test_successful_transcription_is_inserted_and_flagged_to_process() -> None:
    chat_id = 6001
    bot = FakeVoiceBot()
    transcriber = FakeTranscriber(text="anota: comprar pan")
    update = Update.model_validate(
        _make_voice_update(update_id=1, user_id=chat_id, chat_id=chat_id, duration=5),
        context={"bot": bot},
    )
    assert update.message is not None

    outcome = await ingest_incoming(
        bot=bot,
        chat_id=chat_id,
        telegram_update_id=update.update_id,
        message=update.message,
        settings=get_settings(),
        transcriber=transcriber,
    )

    assert transcriber.calls == 1
    assert outcome.should_process is True
    assert outcome.row is not None
    assert outcome.reply_text is None
    text_block = outcome.row.content[0]
    assert text_block["type"] == "text"
    assert "anota: comprar pan" in text_block["text"]
    assert "nota de voz transcrita" in text_block["text"]
    assert await _count_messages(chat_id) == 1


async def test_voice_exceeding_max_duration_is_rejected_without_transcribing() -> None:
    chat_id = 6002
    bot = FakeVoiceBot()
    transcriber = FakeTranscriber(text="no debería llegar aquí")
    settings = get_settings()
    update = Update.model_validate(
        _make_voice_update(
            update_id=2,
            user_id=chat_id,
            chat_id=chat_id,
            duration=settings.voice_max_duration_seconds + 1,
        ),
        context={"bot": bot},
    )
    assert update.message is not None

    outcome = await ingest_incoming(
        bot=bot,
        chat_id=chat_id,
        telegram_update_id=update.update_id,
        message=update.message,
        settings=settings,
        transcriber=transcriber,
    )

    assert transcriber.calls == 0
    assert outcome.row is None
    assert outcome.should_process is False
    assert outcome.reply_text is not None
    assert "segundos" in outcome.reply_text
    assert await _count_messages(chat_id) == 0


async def test_voice_exceeding_max_file_size_is_rejected_without_transcribing() -> None:
    chat_id = 6003
    bot = FakeVoiceBot()
    transcriber = FakeTranscriber(text="no debería llegar aquí")
    settings = get_settings()
    update = Update.model_validate(
        _make_voice_update(
            update_id=3,
            user_id=chat_id,
            chat_id=chat_id,
            duration=5,
            file_size=settings.voice_max_file_bytes + 1,
        ),
        context={"bot": bot},
    )
    assert update.message is not None

    outcome = await ingest_incoming(
        bot=bot,
        chat_id=chat_id,
        telegram_update_id=update.update_id,
        message=update.message,
        settings=settings,
        transcriber=transcriber,
    )

    assert transcriber.calls == 0
    assert outcome.row is None
    assert outcome.should_process is False
    assert await _count_messages(chat_id) == 0


async def test_missing_transcriber_replies_unavailable_without_breaking_text() -> None:
    chat_id = 6004
    bot = FakeVoiceBot()
    settings = get_settings()
    update = Update.model_validate(
        _make_voice_update(update_id=4, user_id=chat_id, chat_id=chat_id, duration=5),
        context={"bot": bot},
    )
    assert update.message is not None

    outcome = await ingest_incoming(
        bot=bot,
        chat_id=chat_id,
        telegram_update_id=update.update_id,
        message=update.message,
        settings=settings,
        transcriber=None,
    )

    assert outcome.row is None
    assert outcome.should_process is False
    assert outcome.reply_text == VOICE_UNAVAILABLE_REPLY
    assert await _count_messages(chat_id) == 0

    # Text messages must keep working in the very same run.
    text_update = Update.model_validate(
        {
            "update_id": 5,
            "message": {
                "message_id": 5,
                "date": 1_700_000_000,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
                "text": "hola",
            },
        },
        context={"bot": bot},
    )
    assert text_update.message is not None
    text_outcome = await ingest_incoming(
        bot=bot,
        chat_id=chat_id,
        telegram_update_id=text_update.update_id,
        message=text_update.message,
        settings=settings,
        transcriber=None,
    )
    assert text_outcome.should_process is True
    assert text_outcome.row is not None


async def test_transcription_failure_replies_error_and_inserts_nothing() -> None:
    chat_id = 6005
    bot = FakeVoiceBot()
    transcriber = FakeTranscriber(error=RuntimeError("boom"))
    update = Update.model_validate(
        _make_voice_update(update_id=6, user_id=chat_id, chat_id=chat_id, duration=5),
        context={"bot": bot},
    )
    assert update.message is not None

    outcome = await ingest_incoming(
        bot=bot,
        chat_id=chat_id,
        telegram_update_id=update.update_id,
        message=update.message,
        settings=get_settings(),
        transcriber=transcriber,
    )

    assert transcriber.calls == 1
    assert outcome.row is None
    assert outcome.should_process is False
    assert outcome.reply_text == VOICE_TRANSCRIPTION_ERROR_REPLY
    assert await _count_messages(chat_id) == 0
