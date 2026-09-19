"""Persistence and history assembly for the `messages` table.

Every turn of the conversation is stored here, including the assistant's `tool_use`
blocks and the `tool_result` blocks sent back to the API, so the exact context of a
conversation can always be reconstructed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message


class HistoryMessage(TypedDict):
    """The subset of a stored message the Anthropic API needs: role + content blocks."""

    role: str
    content: list[dict[str, Any]]


async def insert_user_message(
    session: AsyncSession,
    *,
    chat_id: int,
    telegram_update_id: int | None,
    content: list[dict[str, Any]],
    text_preview: str | None,
) -> Message | None:
    """Insert an incoming user message. Returns None if telegram_update_id already
    exists (Telegram retried a webhook delivery) — the caller should treat that as a
    no-op, not an error.
    """
    stmt = (
        pg_insert(Message)
        .values(
            chat_id=chat_id,
            telegram_update_id=telegram_update_id,
            role="user",
            content=content,
            text_preview=text_preview,
        )
        .on_conflict_do_nothing(index_elements=["telegram_update_id"])
        .returning(Message)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalars().one_or_none()


async def insert_assistant_message(
    session: AsyncSession,
    *,
    chat_id: int,
    content: list[dict[str, Any]],
    text_preview: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    model: str | None,
) -> Message:
    message = Message(
        chat_id=chat_id,
        telegram_update_id=None,
        role="assistant",
        content=content,
        text_preview=text_preview,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        model=model,
    )
    session.add(message)
    await session.flush()
    return message


async def insert_tool_result_message(
    session: AsyncSession,
    *,
    chat_id: int,
    content: list[dict[str, Any]],
) -> Message:
    """Persist the `tool_result` blocks produced after executing tool calls. These are
    sent back to the API as a `role=user` message, per the Messages API contract.
    """
    message = Message(
        chat_id=chat_id,
        telegram_update_id=None,
        role="user",
        content=content,
        text_preview=None,
    )
    session.add(message)
    await session.flush()
    return message


async def get_recent_messages(session: AsyncSession, *, chat_id: int, limit: int) -> list[Message]:
    """Last `limit` messages of the chat, in chronological order."""
    stmt = (
        select(Message).where(Message.chat_id == chat_id).order_by(Message.id.desc()).limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()
    return rows


def _is_user_text_message(entry: HistoryMessage) -> bool:
    """True for a genuine human message: role=user with at least one text block.

    False for a `tool_result` relay (role=user but no text block) and for assistant
    messages — neither is a valid start for a Messages API `messages` array.
    """
    if entry["role"] != "user":
        return False
    return any(block.get("type") == "text" for block in entry["content"])


def trim_history(entries: Sequence[HistoryMessage], max_messages: int) -> list[HistoryMessage]:
    """Keep at most `max_messages`, never starting the window on an orphaned
    `tool_result` or on an assistant message — that would send the API a `tool_result`
    whose matching `tool_use` got cut off, which the API rejects. When the cut lands in
    the middle of such a pair, walk forward until a real user text message anchors the
    window (per SPEC.md section 6).
    """
    window = list(entries[-max_messages:]) if max_messages > 0 else []
    while window and not _is_user_text_message(window[0]):
        window.pop(0)
    return window


async def get_history_for_agent(
    session: AsyncSession, *, chat_id: int, max_messages: int
) -> list[HistoryMessage]:
    rows = await get_recent_messages(session, chat_id=chat_id, limit=max_messages)
    entries: list[HistoryMessage] = [{"role": row.role, "content": row.content} for row in rows]
    return trim_history(entries, max_messages)
