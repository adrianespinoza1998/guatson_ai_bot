"""The agent loop, driven by a fake Anthropic client — no real API calls."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.agent import run_turn
from app.repositories.messages import get_recent_messages, insert_user_message

pytestmark = pytest.mark.asyncio


class FakeBlock:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, Any]:
        return dict(self._data)


class FakeUsage:
    def __init__(self) -> None:
        self.input_tokens = 10
        self.output_tokens = 5
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0


class FakeResponse:
    def __init__(self, blocks: list[dict[str, Any]], *, model: str = "claude-sonnet-5") -> None:
        self.content = [FakeBlock(block) for block in blocks]
        self.usage = FakeUsage()
        self.model = model


class FakeClaudeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def create_message(self, messages: list[dict[str, Any]]) -> FakeResponse:
        self.calls.append(messages)
        return self._responses.pop(0)


async def _seed_user_message(
    session: AsyncSession, *, chat_id: int, update_id: int, text: str
) -> None:
    inserted = await insert_user_message(
        session,
        chat_id=chat_id,
        telegram_update_id=update_id,
        content=[{"type": "text", "text": text}],
        text_preview=text,
    )
    assert inserted is not None


async def test_tool_use_executes_tool_and_saves_turns_in_order(db_session: AsyncSession) -> None:
    chat_id = 5001
    await _seed_user_message(db_session, chat_id=chat_id, update_id=111, text="anota: comprar pan")

    client = FakeClaudeClient(
        [
            FakeResponse(
                [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "create_task",
                        "input": {"title": "Comprar pan"},
                    }
                ]
            ),
            FakeResponse([{"type": "text", "text": "Anotado: comprar pan."}]),
        ]
    )

    result = await run_turn(
        session=db_session,
        settings=get_settings(),
        client=client,
        chat_id=chat_id,
        now=dt.datetime.now(dt.UTC),
    )

    assert result.hit_iteration_limit is False
    assert result.final_message.content[0]["text"] == "Anotado: comprar pan."
    assert len(result.saved_artifacts) == 0

    rows = await get_recent_messages(db_session, chat_id=chat_id, limit=10)
    assert [row.role for row in rows] == ["user", "assistant", "user", "assistant"]
    assert rows[1].content[0]["type"] == "tool_use"
    assert rows[2].content[0]["type"] == "tool_result"
    assert rows[2].content[0]["is_error"] is False
    assert rows[3].content[0]["text"] == "Anotado: comprar pan."


async def test_respects_agent_max_iterations(db_session: AsyncSession) -> None:
    chat_id = 5002
    await _seed_user_message(db_session, chat_id=chat_id, update_id=222, text="haz muchas cosas")

    # Every response keeps calling a tool, never producing a final text answer.
    responses = [
        FakeResponse([{"type": "tool_use", "id": f"tu_{i}", "name": "list_artifacts", "input": {}}])
        for i in range(5)
    ]
    client = FakeClaudeClient(responses)
    settings = get_settings().model_copy(update={"agent_max_iterations": 2})

    result = await run_turn(
        session=db_session,
        settings=settings,
        client=client,
        chat_id=chat_id,
        now=dt.datetime.now(dt.UTC),
    )

    assert result.hit_iteration_limit is True
    assert len(client.calls) == 2

    rows = await get_recent_messages(db_session, chat_id=chat_id, limit=10)
    # Every tool_use has its tool_result: no dangling pair even at the iteration cap.
    assert [row.role for row in rows] == ["user", "assistant", "user", "assistant", "user"]


async def test_invalid_tool_input_produces_an_is_error_tool_result(
    db_session: AsyncSession,
) -> None:
    chat_id = 5003
    await _seed_user_message(db_session, chat_id=chat_id, update_id=333, text="crea algo")

    client = FakeClaudeClient(
        [
            # "title" is required by create_task's input schema and is missing here.
            FakeResponse([{"type": "tool_use", "id": "tu_1", "name": "create_task", "input": {}}]),
            FakeResponse([{"type": "text", "text": "No pude crear la tarea."}]),
        ]
    )

    await run_turn(
        session=db_session,
        settings=get_settings(),
        client=client,
        chat_id=chat_id,
        now=dt.datetime.now(dt.UTC),
    )

    rows = await get_recent_messages(db_session, chat_id=chat_id, limit=10)
    tool_result_row = rows[2]
    assert tool_result_row.content[0]["type"] == "tool_result"
    assert tool_result_row.content[0]["is_error"] is True
