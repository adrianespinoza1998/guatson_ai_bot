#!/usr/bin/env python3
"""Local development entrypoint: long-polling instead of a webhook, so the bot can be
tested end-to-end without exposing a public URL. Not used in staging/production —
those run app.main:app behind a real webhook (see scripts/set_webhook.py).
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher

from app.config import get_settings
from app.llm.client import ClaudeClient
from app.logging import configure_logging, get_logger
from app.telegram.handlers import router

logger = get_logger(__name__)


async def main() -> None:
    configure_logging()
    settings = get_settings()

    bot = Bot(token=settings.telegram_bot_token)
    claude_client = ClaudeClient(settings)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    try:
        # Polling and a registered webhook are mutually exclusive for the same bot
        # token; clear any previously-set webhook (e.g. from staging/production)
        # before polling.
        await bot.delete_webhook(drop_pending_updates=False)

        logger.info("polling_started")
        await dispatcher.start_polling(bot, settings=settings, client=claude_client)
    finally:
        await claude_client.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
