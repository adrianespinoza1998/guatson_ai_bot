"""Sending replies back to Telegram: splitting long text and attaching artifacts."""

from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile

TELEGRAM_MESSAGE_LIMIT = 4096

_LANGUAGE_EXTENSIONS = {
    "python": "py",
    "javascript": "js",
    "typescript": "ts",
    "bash": "sh",
    "shell": "sh",
    "sh": "sh",
    "sql": "sql",
    "html": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "go": "go",
    "rust": "rs",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "ruby": "rb",
    "php": "php",
    "markdown": "md",
    "text": "txt",
}


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split `text` into chunks of at most `limit` characters, preferring to break on
    a newline so code blocks don't get cut mid-line when avoidable.
    """
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    chunks.append(remaining)
    return chunks


def artifact_filename(name: str, language: str) -> str:
    extension = _LANGUAGE_EXTENSIONS.get(language.strip().lower(), "txt")
    stem = name if "." not in name else name.rsplit(".", 1)[0]
    return f"{stem}.{extension}"


async def send_typing(bot: Bot, chat_id: int) -> None:
    await bot.send_chat_action(chat_id, ChatAction.TYPING)


async def send_text(bot: Bot, chat_id: int, text: str) -> None:
    for chunk in split_message(text):
        if chunk:
            await bot.send_message(chat_id, chunk)


async def send_artifact_document(
    bot: Bot, chat_id: int, *, name: str, language: str, code: str
) -> None:
    document = BufferedInputFile(code.encode("utf-8"), filename=artifact_filename(name, language))
    await bot.send_document(chat_id, document=document)
