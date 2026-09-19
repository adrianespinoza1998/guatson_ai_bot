"""Persistence for the `tasks` table. Every query is scoped to a `chat_id`."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task


async def create_task(
    session: AsyncSession,
    *,
    chat_id: int,
    title: str,
    description: str | None = None,
    due_at: dt.datetime | None = None,
    tags: list[str] | None = None,
) -> Task:
    task = Task(
        chat_id=chat_id,
        title=title,
        description=description,
        due_at=due_at,
        tags=tags or [],
    )
    session.add(task)
    await session.flush()
    return task


async def search_tasks(
    session: AsyncSession,
    *,
    chat_id: int,
    query: str | None = None,
    status: str | None = "open",
    tags: list[str] | None = None,
    limit: int = 10,
) -> list[Task]:
    stmt = select(Task).where(Task.chat_id == chat_id)

    if status is not None:
        stmt = stmt.where(Task.status == status)
    if tags:
        stmt = stmt.where(Task.tags.overlap(tags))

    if query:
        # `search` folds accents via immutable_unaccent (see migrations/versions/
        # 0001_initial.py); the query side must go through the same function so
        # "renovacion" and "renovación" match the same rows.
        ts_query = func.websearch_to_tsquery(
            "spanish", func.immutable_unaccent(query)
        )
        stmt = stmt.where(Task.search.op("@@")(ts_query))
        stmt = stmt.order_by(func.ts_rank(Task.search, ts_query).desc(), Task.id.desc())
    else:
        stmt = stmt.order_by(Task.id.desc())

    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_task(session: AsyncSession, *, chat_id: int, task_id: int) -> Task | None:
    stmt = select(Task).where(Task.chat_id == chat_id, Task.id == task_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_task(
    session: AsyncSession,
    *,
    chat_id: int,
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    due_at: dt.datetime | None = None,
    tags: list[str] | None = None,
) -> Task | None:
    task = await get_task(session, chat_id=chat_id, task_id=task_id)
    if task is None:
        return None

    if title is not None:
        task.title = title
    if description is not None:
        task.description = description
    if due_at is not None:
        task.due_at = due_at
    if tags is not None:
        task.tags = tags
    if status is not None:
        task.status = status
        task.completed_at = dt.datetime.now(dt.UTC) if status == "done" else None

    await session.flush()
    return task
