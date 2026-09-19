"""`create_task`, `search_tasks`, `update_task` tool implementations."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app.repositories import tasks as tasks_repo
from app.tools.base import ToolError

if TYPE_CHECKING:
    from app.models import Task
    from app.tools.base import ToolContext

# Used to interpret a due date/time the user gave without an explicit UTC offset.
DEFAULT_TIMEZONE = ZoneInfo("America/Santiago")


def parse_due_at(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"due_at inválido, se esperaba una fecha/hora ISO 8601: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_TIMEZONE)
    return parsed.astimezone(dt.UTC)


def _task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "tags": list(task.tags),
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


class CreateTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    due_at: str | None = Field(
        default=None, description="Fecha/hora en formato ISO 8601, ej. 2026-09-25T09:00:00"
    )
    tags: list[str] = Field(default_factory=list, max_length=20)


async def create_task(ctx: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
    payload = CreateTaskInput.model_validate(data)
    due_at = parse_due_at(payload.due_at)
    task = await tasks_repo.create_task(
        ctx.session,
        chat_id=ctx.chat_id,
        title=payload.title,
        description=payload.description,
        due_at=due_at,
        tags=payload.tags,
    )
    return _task_to_dict(task)


class SearchTasksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=300)
    status: Literal["open", "done", "cancelled"] | None = "open"
    tags: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=10, ge=1, le=25)


async def search_tasks(ctx: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
    payload = SearchTasksInput.model_validate(data)
    results = await tasks_repo.search_tasks(
        ctx.session,
        chat_id=ctx.chat_id,
        query=payload.query,
        status=payload.status,
        tags=payload.tags or None,
        limit=payload.limit,
    )
    return {"tasks": [_task_to_dict(task) for task in results], "count": len(results)}


class UpdateTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: int
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    status: Literal["open", "done", "cancelled"] | None = None
    due_at: str | None = None
    tags: list[str] | None = Field(default=None, max_length=20)


async def update_task(ctx: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
    payload = UpdateTaskInput.model_validate(data)
    due_at = parse_due_at(payload.due_at) if payload.due_at is not None else None
    task = await tasks_repo.update_task(
        ctx.session,
        chat_id=ctx.chat_id,
        task_id=payload.task_id,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        due_at=due_at,
        tags=payload.tags,
    )
    if task is None:
        raise ToolError(f"No existe la tarea {payload.task_id} en este chat.")
    return _task_to_dict(task)
