"""Application settings, loaded from environment variables (or a local .env file)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_user_ids(value: object) -> list[int]:
    if isinstance(value, str):
        return [int(item.strip()) for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [int(item) for item in value]
    raise TypeError(f"Cannot parse a list of Telegram user ids from {value!r}")


TelegramUserIds = Annotated[list[int], NoDecode, BeforeValidator(_parse_user_ids)]


class Settings(BaseSettings):
    """Typed, validated configuration. The app refuses to start if a required value is missing."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["local", "staging", "production"] = "local"

    database_url: str

    telegram_bot_token: str
    telegram_webhook_secret: str
    telegram_allowed_user_ids: TelegramUserIds
    public_base_url: str

    anthropic_api_key: str
    claude_model: str = "claude-sonnet-5"
    claude_max_tokens: int = 4096

    history_max_messages: int = 40
    agent_max_iterations: int = 8

    # Guards against abusive/oversized payloads (see docs/DECISIONS.md).
    webhook_max_body_bytes: int = 1 * 1024 * 1024
    artifact_max_code_bytes: int = 200 * 1024

    # Voice-note transcription (see docs/specs/audio-transcription.md). Unlike the
    # other external-service keys, this one is optional: the bot is fully usable for
    # text without it, so a missing key must not block startup — only voice notes.
    openai_api_key: str | None = None
    openai_transcription_model: str = "whisper-1"
    voice_max_duration_seconds: int = 120
    voice_max_file_bytes: int = 20 * 1024 * 1024

    # Web dashboard (see docs/specs/dashboard.md). Also optional: without a password
    # set, the dashboard routes don't exist (404), so it's opt-in per install.
    dashboard_password: str | None = None
    dashboard_usage_days: int = 30


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings. Call ``get_settings.cache_clear()`` to force a reload."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
