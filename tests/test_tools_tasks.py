"""create_task / search_tasks / update_task against a real Postgres."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.tools.base import ToolContext
from app.tools.tasks import create_task, search_tasks, update_task

pytestmark = pytest.mark.asyncio


@pytest.fixture
def make_ctx(db_session: AsyncSession) -> Callable[[int], ToolContext]:
    def _make(chat_id: int) -> ToolContext:
        return ToolContext(session=db_session, chat_id=chat_id, settings=get_settings())

    return _make


async def test_create_task(make_ctx: Callable[[int], ToolContext]) -> None:
    ctx = make_ctx(1001)
    result = await create_task(
        ctx,
        {
            "title": "Renovar el certificado SSL",
            "description": "Vence el viernes",
            "tags": ["infraestructura"],
        },
    )
    assert result["status"] == "open"
    assert result["title"] == "Renovar el certificado SSL"
    assert result["tags"] == ["infraestructura"]


async def test_search_finds_by_title_and_defaults_to_open(
    make_ctx: Callable[[int], ToolContext],
) -> None:
    ctx = make_ctx(1002)
    await create_task(ctx, {"title": "Renovar certificado SSL"})
    await create_task(ctx, {"title": "Comprar café"})

    result = await search_tasks(ctx, {"query": "certificado"})
    assert result["count"] == 1
    assert result["tasks"][0]["title"] == "Renovar certificado SSL"


async def test_search_is_accent_insensitive(make_ctx: Callable[[int], ToolContext]) -> None:
    ctx = make_ctx(1003)
    await create_task(ctx, {"title": "Planificar la renovación del dominio"})

    with_accent = await search_tasks(ctx, {"query": "renovación"})
    without_accent = await search_tasks(ctx, {"query": "renovacion"})

    assert with_accent["count"] == 1
    assert without_accent["count"] == 1
    assert with_accent["tasks"][0]["id"] == without_accent["tasks"][0]["id"]


async def test_search_excludes_done_tasks_by_default(
    make_ctx: Callable[[int], ToolContext],
) -> None:
    ctx = make_ctx(1004)
    created = await create_task(ctx, {"title": "Pagar hosting"})
    await update_task(ctx, {"task_id": created["id"], "status": "done"})

    open_results = await search_tasks(ctx, {"status": "open"})
    done_results = await search_tasks(ctx, {"status": "done"})

    assert created["id"] not in [t["id"] for t in open_results["tasks"]]
    assert created["id"] in [t["id"] for t in done_results["tasks"]]


async def test_update_task_completes_and_sets_completed_at(
    make_ctx: Callable[[int], ToolContext],
) -> None:
    ctx = make_ctx(1005)
    created = await create_task(ctx, {"title": "Backup semanal"})
    assert created["completed_at"] is None

    updated = await update_task(ctx, {"task_id": created["id"], "status": "done"})
    assert updated["status"] == "done"
    assert updated["completed_at"] is not None


async def test_tasks_are_isolated_by_chat_id(make_ctx: Callable[[int], ToolContext]) -> None:
    ctx_a = make_ctx(2001)
    ctx_b = make_ctx(2002)
    created = await create_task(ctx_a, {"title": "Tarea exclusiva de un chat"})

    results_b = await search_tasks(ctx_b, {"status": None})
    assert created["id"] not in [t["id"] for t in results_b["tasks"]]

    from app.tools.base import ToolError

    with pytest.raises(ToolError):
        await update_task(ctx_b, {"task_id": created["id"], "status": "done"})
