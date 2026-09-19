#!/usr/bin/env python3
"""Registers (or re-registers) the Telegram webhook for this bot, using
PUBLIC_BASE_URL and TELEGRAM_WEBHOOK_SECRET from the environment. Safe to run more
than once: setWebhook simply overwrites the previous registration with the same
values (idempotent).
"""

from __future__ import annotations

import asyncio

from aiogram import Bot

from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    bot = Bot(token=settings.telegram_bot_token)
    try:
        webhook_url = f"{settings.public_base_url.rstrip('/')}/webhook/telegram"
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=["message"],
        )
        info = await bot.get_webhook_info()
        print(info.model_dump_json(indent=2))
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
