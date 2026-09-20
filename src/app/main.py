"""FastAPI application factory: lifespan (Bot, Claude client), middleware, routes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from aiogram import Bot
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.dashboard.routes import router as dashboard_router
from app.db import dispose_engine, get_engine
from app.llm.client import ClaudeClient
from app.logging import configure_logging, get_logger
from app.telegram.webhook import router as webhook_router
from app.transcription import create_transcriber

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()

    bot = Bot(token=settings.telegram_bot_token)
    claude_client = ClaudeClient(settings)
    transcriber = create_transcriber(settings)

    app.state.settings = settings
    app.state.bot = bot
    app.state.claude_client = claude_client
    app.state.transcriber = transcriber

    logger.info("app_started", app_env=settings.app_env, voice_enabled=transcriber is not None)
    try:
        yield
    finally:
        await bot.session.close()
        await claude_client.aclose()
        if transcriber is not None:
            await transcriber.aclose()
        await dispose_engine()
        logger.info("app_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="guatson-bot-ai", lifespan=lifespan)

    @app.middleware("http")
    async def limit_body_size(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > settings.webhook_max_body_bytes:
            return JSONResponse({"detail": "payload too large"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready(response: Response) -> dict[str, str]:
        try:
            engine = get_engine()
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            logger.exception("readiness_check_failed")
            response.status_code = 503
            return {"status": "unavailable"}
        return {"status": "ok"}

    app.include_router(webhook_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
