"""Voice-note transcription via OpenAI's audio transcriptions API.

See docs/specs/audio-transcription.md. `OPENAI_API_KEY` is optional at the app level
(unlike the other external-service keys): `create_transcriber` returns None when it
isn't configured, and callers must handle that by telling the user transcription isn't
available rather than failing the whole bot.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import Settings


class Transcriber:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def transcribe(self, audio_bytes: bytes, *, filename: str = "voice.ogg") -> str:
        transcription = await self._client.audio.transcriptions.create(
            model=self._settings.openai_transcription_model,
            file=(filename, audio_bytes, "audio/ogg"),
        )
        return transcription.text

    async def aclose(self) -> None:
        await self._client.close()


def create_transcriber(settings: Settings) -> Transcriber | None:
    if not settings.openai_api_key:
        return None
    return Transcriber(settings)
