"""POST /webhook/telegram — see SPEC.md section 6 for the exact step-by-step contract."""

from __future__ import annotations

import hmac
import json

from aiogram import Bot
from aiogram.types import Update
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from pydantic import ValidationError

from app.config import Settings
from app.llm.client import ClaudeClient
from app.queue import BackgroundTasksQueue
from app.telegram.handlers import handle_incoming

router = APIRouter()


def get_bot(request: Request) -> Bot:
    return request.app.state.bot  # type: ignore[no-any-return]


def get_claude_client(request: Request) -> ClaudeClient:
    return request.app.state.claude_client  # type: ignore[no-any-return]


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    bot: Bot = Depends(get_bot),
    client: ClaudeClient = Depends(get_claude_client),
    settings: Settings = Depends(get_app_settings),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    # Constant-time comparison: a timing side-channel here would let an attacker
    # recover the secret token one character at a time.
    if not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="invalid secret token")

    body = await request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc

    try:
        update = Update.model_validate(data, context={"bot": bot})
    except ValidationError as exc:
        # Well-formed JSON that isn't a Telegram Update — not something Telegram
        # itself should ever send, but a malformed request shouldn't 500.
        raise HTTPException(status_code=400, detail="invalid Telegram update") from exc
    if update.message is None:
        # allowed_updates=["message"] means this shouldn't happen in practice; ignore
        # defensively rather than error, so an unexpected update type never breaks
        # webhook delivery for Telegram's retry logic.
        return Response(status_code=200)

    # Not in an allow-listed chat, or a retried update_id we already stored: both are
    # silent no-ops by design (see app.telegram.handlers.handle_incoming). Whatever the
    # outcome, Telegram must see 200 so it doesn't retry forever.
    await handle_incoming(
        bot=bot,
        message=update.message,
        telegram_update_id=update.update_id,
        settings=settings,
        client=client,
        queue=BackgroundTasksQueue(background_tasks),
    )
    return Response(status_code=200)
