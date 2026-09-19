"""POST /webhook/telegram, end-to-end through the real FastAPI app — with a fake Bot
and a fake Claude client swapped in via FastAPI dependency overrides, so no real
Telegram or Anthropic call ever happens.

Uses httpx's ASGI transport directly (not fastapi.testclient.TestClient): TestClient
drives the app from a separate thread with its own event loop, which fights our
single-event-loop-per-test-session setup (see pyproject.toml) and the process-wide
async DB engine singleton. httpx.AsyncClient runs on the same loop as the test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from aiogram import Bot
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_session_factory
from app.main import app
from app.models import Message
from app.telegram.webhook import get_bot, get_claude_client
from tests.test_agent_loop import FakeClaudeClient, FakeResponse


class FakeBot(Bot):
    """A real aiogram Bot (so type-based checks during Update parsing still pass), but
    every method that would hit the Telegram API is overridden to just record the call.
    """

    def __init__(self) -> None:
        super().__init__(token="123456789:AAFakeTestTokenNotReal12345678901")
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:  # type: ignore[override]
        self.sent_messages.append((chat_id, text))

    async def send_chat_action(self, chat_id: int, action: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None

    async def send_document(self, chat_id: int, document: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None


def _make_update(*, update_id: int, user_id: int, chat_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "text": text,
        },
    }


async def _count_messages(chat_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(func.count()).select_from(Message).where(Message.chat_id == chat_id)
        )
        return result.scalar_one()


@pytest_asyncio.fixture
async def fake_bot() -> FakeBot:
    return FakeBot()


@pytest_asyncio.fixture
async def fake_claude() -> FakeClaudeClient:
    return FakeClaudeClient([FakeResponse([{"type": "text", "text": "Listo."}])])


@pytest_asyncio.fixture
async def client(fake_bot: FakeBot, fake_claude: FakeClaudeClient) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_bot] = lambda: fake_bot
    app.dependency_overrides[get_claude_client] = lambda: fake_claude
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    app.dependency_overrides.clear()


async def test_wrong_secret_returns_403(client: AsyncClient) -> None:
    response = await client.post(
        "/webhook/telegram",
        json=_make_update(update_id=1, user_id=1001, chat_id=1001, text="hola"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


async def test_disallowed_user_gets_200_with_no_effects(client: AsyncClient) -> None:
    settings = get_settings()
    not_allowed_user_id = 999999

    response = await client.post(
        "/webhook/telegram",
        json=_make_update(
            update_id=2, user_id=not_allowed_user_id, chat_id=not_allowed_user_id, text="hola"
        ),
        headers={"X-Telegram-Bot-Api-Secret-Token": settings.telegram_webhook_secret},
    )

    assert response.status_code == status.HTTP_200_OK
    assert await _count_messages(not_allowed_user_id) == 0


async def test_duplicate_update_id_is_processed_once(
    client: AsyncClient, fake_bot: FakeBot, fake_claude: FakeClaudeClient
) -> None:
    settings = get_settings()
    allowed_user_id = 1001
    update = _make_update(
        update_id=3, user_id=allowed_user_id, chat_id=allowed_user_id, text="anota: comprar pan"
    )
    headers = {"X-Telegram-Bot-Api-Secret-Token": settings.telegram_webhook_secret}

    first = await client.post("/webhook/telegram", json=update, headers=headers)
    second = await client.post("/webhook/telegram", json=update, headers=headers)

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK

    # One user message + one assistant reply — the retried delivery inserted nothing
    # and never re-ran the (fake) agent turn.
    assert await _count_messages(allowed_user_id) == 2
    assert len(fake_claude.calls) == 1
    assert len(fake_bot.sent_messages) == 1
