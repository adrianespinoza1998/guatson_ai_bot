"""The tool-use agent loop.

Runs entirely inside the caller's DB transaction (see app.db.session_scope): every
assistant turn and every tool_result turn is `flush()`-ed as it happens, but nothing is
committed until the whole turn finishes successfully. If anything unexpected blows up
midway — an API error, a bug in a tool handler — the exception propagates, the
transaction rolls back, and the conversation is left exactly as it was before this turn
started: never with an assistant `tool_use` saved without its matching `tool_result`.
See docs/DECISIONS.md.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm.prompts import date_context_line
from app.models import Message
from app.repositories import messages as messages_repo
from app.repositories.messages import HistoryMessage
from app.tools.registry import ToolContext, dispatch


class AgentError(Exception):
    """Raised for a precondition the caller is expected to guarantee (e.g. there must
    already be a user message in history before running a turn).
    """


class AnthropicResponseLike(Protocol):
    """The subset of anthropic.types.Message that the agent loop relies on. Lets tests
    pass a fake client without depending on the real SDK types.
    """

    model: str

    @property
    def content(self) -> list[Any]: ...

    @property
    def usage(self) -> Any: ...


class ClaudeClientLike(Protocol):
    async def create_message(self, messages: list[dict[str, Any]]) -> AnthropicResponseLike: ...


@dataclass
class AgentResult:
    final_message: Message
    hit_iteration_limit: bool
    saved_artifacts: list[dict[str, Any]] = field(default_factory=list)


def _with_date_prefix(entry: HistoryMessage, now: dt.datetime) -> HistoryMessage:
    prefix = date_context_line(now)
    content = [dict(block) for block in entry["content"]]
    for block in content:
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            block["text"] = f"{prefix}\n\n{block['text']}"
            break
    return {"role": entry["role"], "content": content}


def _text_preview(blocks: list[dict[str, Any]]) -> str | None:
    texts = [b["text"] for b in blocks if b.get("type") == "text" and b.get("text")]
    if not texts:
        return None
    preview = " ".join(texts).strip()
    return preview[:2000] or None


async def run_turn(
    *,
    session: AsyncSession,
    settings: Settings,
    client: ClaudeClientLike,
    chat_id: int,
    now: dt.datetime,
) -> AgentResult:
    history = await messages_repo.get_history_for_agent(
        session, chat_id=chat_id, max_messages=settings.history_max_messages
    )
    if not history:
        raise AgentError(
            f"No hay historial para chat_id={chat_id}; se esperaba el mensaje de "
            "usuario recién insertado."
        )

    history = list(history)
    history[-1] = _with_date_prefix(history[-1], now)
    api_messages: list[dict[str, Any]] = [dict(entry) for entry in history]

    last_assistant_row: Message | None = None
    saved_artifacts: list[dict[str, Any]] = []

    for _ in range(settings.agent_max_iterations):
        response = await client.create_message(api_messages)
        content_blocks = [block.model_dump() for block in response.content]
        usage = response.usage

        assistant_row = await messages_repo.insert_assistant_message(
            session,
            chat_id=chat_id,
            content=content_blocks,
            text_preview=_text_preview(content_blocks),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None),
            model=response.model,
        )
        last_assistant_row = assistant_row
        api_messages.append({"role": "assistant", "content": content_blocks})

        tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
        if not tool_uses:
            return AgentResult(
                final_message=assistant_row,
                hit_iteration_limit=False,
                saved_artifacts=saved_artifacts,
            )

        tool_result_blocks: list[dict[str, Any]] = []
        for call in tool_uses:
            ctx = ToolContext(
                session=session,
                chat_id=chat_id,
                settings=settings,
                assistant_message_id=assistant_row.id,
            )
            result_text, is_error = await dispatch(ctx, call["name"], call.get("input") or {})
            if call["name"] == "save_artifact" and not is_error:
                saved_artifacts.append(json.loads(result_text))
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": result_text,
                    "is_error": is_error,
                }
            )

        await messages_repo.insert_tool_result_message(
            session, chat_id=chat_id, content=tool_result_blocks
        )
        api_messages.append({"role": "user", "content": tool_result_blocks})

    assert last_assistant_row is not None
    return AgentResult(
        final_message=last_assistant_row,
        hit_iteration_limit=True,
        saved_artifacts=saved_artifacts,
    )
