"""save_artifact / get_artifact / list_artifacts against a real Postgres."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.tools.artifacts import get_artifact, list_artifacts, save_artifact
from app.tools.base import ToolContext, ToolError

pytestmark = pytest.mark.asyncio


@pytest.fixture
def make_ctx(db_session: AsyncSession) -> Callable[[int], ToolContext]:
    def _make(chat_id: int) -> ToolContext:
        return ToolContext(session=db_session, chat_id=chat_id, settings=get_settings())

    return _make


async def test_save_artifact_creates_version_1(make_ctx: Callable[[int], ToolContext]) -> None:
    ctx = make_ctx(3001)
    result = await save_artifact(
        ctx,
        {
            "name": "contar_lineas",
            "language": "python",
            "code": "print('v1')\n",
            "description": "Cuenta líneas de un archivo.",
        },
    )
    assert result["version"] == 1
    assert result["code"] == "print('v1')\n"


async def test_save_artifact_again_creates_version_2(
    make_ctx: Callable[[int], ToolContext],
) -> None:
    ctx = make_ctx(3002)
    await save_artifact(ctx, {"name": "script", "language": "python", "code": "print(1)\n"})
    second = await save_artifact(
        ctx, {"name": "script", "language": "python", "code": "print(2)\n"}
    )

    assert second["version"] == 2
    assert second["code"] == "print(2)\n"


async def test_get_artifact_returns_latest_by_default(
    make_ctx: Callable[[int], ToolContext],
) -> None:
    ctx = make_ctx(3003)
    await save_artifact(ctx, {"name": "script", "language": "python", "code": "print(1)\n"})
    await save_artifact(ctx, {"name": "script", "language": "python", "code": "print(2)\n"})

    latest = await get_artifact(ctx, {"name": "script"})
    assert latest["version"] == 2
    assert latest["code"] == "print(2)\n"


async def test_get_artifact_returns_specific_version(
    make_ctx: Callable[[int], ToolContext],
) -> None:
    ctx = make_ctx(3004)
    await save_artifact(ctx, {"name": "script", "language": "python", "code": "print(1)\n"})
    await save_artifact(ctx, {"name": "script", "language": "python", "code": "print(2)\n"})

    first = await get_artifact(ctx, {"name": "script", "version": 1})
    assert first["version"] == 1
    assert first["code"] == "print(1)\n"


async def test_get_artifact_missing_raises_tool_error(
    make_ctx: Callable[[int], ToolContext],
) -> None:
    ctx = make_ctx(3005)
    with pytest.raises(ToolError):
        await get_artifact(ctx, {"name": "no-existe"})


async def test_save_artifact_rejects_code_over_the_size_limit(
    make_ctx: Callable[[int], ToolContext],
) -> None:
    ctx = make_ctx(3006)
    too_big = "x" * (ctx.settings.artifact_max_code_bytes + 1)
    with pytest.raises(ToolError):
        await save_artifact(ctx, {"name": "gigante", "language": "python", "code": too_big})


async def test_artifacts_are_isolated_by_chat_id(make_ctx: Callable[[int], ToolContext]) -> None:
    ctx_a = make_ctx(4001)
    ctx_b = make_ctx(4002)
    await save_artifact(ctx_a, {"name": "script", "language": "python", "code": "print('a')\n"})

    with pytest.raises(ToolError):
        await get_artifact(ctx_b, {"name": "script"})

    listing = await list_artifacts(ctx_b, {})
    assert listing["count"] == 0
