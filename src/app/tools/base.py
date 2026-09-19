"""Shared types for tool implementations. Kept dependency-free (no imports of
individual tool modules) so both the tool modules and the registry can import from
here without a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings


class ToolError(Exception):
    """Raised by a tool handler for an expected, user-facing failure (bad input,
    not-found, size limit, etc). The registry's dispatch() catches it and turns it
    into a `tool_result` with `is_error: true` — it never reaches the agent loop as an
    unhandled exception.
    """


@dataclass
class ToolContext:
    """Everything a tool handler needs, injected by the agent loop. `chat_id` is never
    provided by the model — the backend controls it, so a tool call can never read or
    write another chat's data.
    """

    session: AsyncSession
    chat_id: int
    settings: Settings
    assistant_message_id: int | None = None
