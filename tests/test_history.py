"""trim_history() is a pure function — no DB needed."""

from __future__ import annotations

from typing import Any

from app.repositories.messages import HistoryMessage, trim_history


def _msg(
    role: str, *, text: str | None = None, blocks: list[dict[str, Any]] | None = None
) -> HistoryMessage:
    content: list[dict[str, Any]] = list(blocks or [])
    if text is not None:
        content.append({"type": "text", "text": text})
    return {"role": role, "content": content}


def _tool_use() -> dict[str, Any]:
    return {"type": "tool_use", "id": "tu_1", "name": "create_task", "input": {}}


def _tool_result() -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}


def test_keeps_last_n_when_the_window_already_starts_on_user_text() -> None:
    entries = [
        _msg("assistant", text="previo"),
        _msg("user", text="hola"),
        _msg("assistant", text="hi"),
    ]
    # entries[-2:] already starts on a user text message, so nothing gets trimmed off.
    assert trim_history(entries, max_messages=2) == entries[-2:]


def test_backs_off_past_an_orphaned_tool_use_tool_result_pair() -> None:
    entries = [
        _msg("user", text="primera pregunta"),
        _msg("assistant", blocks=[_tool_use()]),
        _msg("user", blocks=[_tool_result()]),
        _msg("assistant", text="respuesta1"),
        _msg("user", text="segunda pregunta"),
        _msg("assistant", blocks=[_tool_use()]),
        _msg("user", blocks=[_tool_result()]),
        _msg("assistant", text="respuesta2"),
    ]
    # A naive last-6 slice would start at index 2 (a tool_result whose tool_use, at
    # index 1, got cut off). The trimmed window must back off to the next real user
    # text message instead — index 4 — even though that means fewer than 6 messages.
    result = trim_history(entries, max_messages=6)
    assert result == entries[4:]
    assert result[0]["role"] == "user"
    assert result[0]["content"][0]["type"] == "text"


def test_never_returns_a_window_starting_on_an_assistant_message() -> None:
    entries = [
        _msg("user", text="primero"),
        _msg("assistant", text="ok"),
        _msg("user", text="segundo"),
    ]
    result = trim_history(entries, max_messages=2)
    assert result[0]["role"] == "user"
    assert result[0]["content"][0]["type"] == "text"


def test_empty_history() -> None:
    assert trim_history([], max_messages=40) == []


def test_zero_max_messages_returns_empty() -> None:
    assert trim_history([_msg("user", text="hola")], max_messages=0) == []


def test_all_messages_fit_within_the_limit() -> None:
    entries = [_msg("user", text="hola"), _msg("assistant", text="hi")]
    assert trim_history(entries, max_messages=40) == entries
