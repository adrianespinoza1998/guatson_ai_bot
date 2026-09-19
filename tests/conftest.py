"""Shared test fixtures.

Tests run against a real Postgres (a service container in CI, docker-compose's `db`
locally) pointed at by DATABASE_URL, with migrations already applied — see
docs/DECISIONS.md. Required settings are given fake-but-valid-shaped values here so
importing `app.*` never needs a real .env; nothing here ever calls the real Anthropic
or Telegram APIs.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://guatson:guatson@localhost:5432/guatson")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:AAFakeTestTokenNotReal12345678901")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("TELEGRAM_ALLOWED_USER_IDS", "1001,1002")
os.environ.setdefault("PUBLIC_BASE_URL", "https://test.invalid")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine, get_session_factory  # noqa: E402
from app.models import Base  # noqa: E402

get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A session backed by a real transaction that is always rolled back, so nothing a
    test writes through this fixture is ever actually persisted.
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables_after_test() -> AsyncGenerator[None, None]:
    """Safety net for tests that exercise the app end-to-end (e.g. via TestClient),
    where the app's own code commits for real instead of going through `db_session`.
    """
    yield
    engine = get_engine()
    async with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            await connection.execute(table.delete())
