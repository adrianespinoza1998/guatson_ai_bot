"""Dashboard routes: read-only views over messages/tasks/artifacts/usage, plus task
completion — the same operation the agent already exposes via the `update_task` tool.
All behind HTTP Basic auth (see app.dashboard.auth). See docs/specs/dashboard.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.dashboard.auth import get_app_settings, require_dashboard_auth
from app.db import session_scope
from app.models import Message as DbMessage
from app.repositories import artifacts as artifacts_repo
from app.repositories import messages as messages_repo
from app.repositories import tasks as tasks_repo
from app.telegram.sender import artifact_filename

router = APIRouter(dependencies=[Depends(require_dashboard_auth)])

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

TASK_STATUSES = ("open", "done", "cancelled")


def _selected_chat_id(chat_id: int | None, chat_ids: list[int]) -> int | None:
    if chat_id is not None and chat_id in chat_ids:
        return chat_id
    return chat_ids[0] if chat_ids else None


def _summarize_message(message: DbMessage) -> dict[str, Any]:
    """A human-readable one-liner per content block — good enough for a debug view,
    not a pretty rendering of tool calls (see docs/specs/dashboard.md).
    """
    parts: list[str] = []
    for block in message.content:
        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type == "tool_use":
            parts.append(f"[tool_use: {block.get('name')}]")
        elif block_type == "tool_result":
            marker = "error" if block.get("is_error") else "ok"
            parts.append(f"[tool_result: {marker}]")
        else:
            parts.append(f"[{block_type}]")
    return {
        "id": message.id,
        "role": message.role,
        "created_at": message.created_at,
        "text": "\n".join(parts),
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(
    request: Request, chat_id: int | None = Query(default=None)
) -> HTMLResponse:
    async with session_scope() as session:
        chat_ids = await messages_repo.list_chat_ids(session)
        selected = _selected_chat_id(chat_id, chat_ids)
        task_counts = {"open": 0, "done": 0, "cancelled": 0}
        recent_messages: list[dict[str, Any]] = []
        if selected is not None:
            task_counts = await tasks_repo.count_tasks_by_status(session, chat_id=selected)
            rows = await messages_repo.get_recent_messages(session, chat_id=selected, limit=10)
            recent_messages = [_summarize_message(row) for row in rows]

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "chat_ids": chat_ids,
            "selected_chat_id": selected,
            "task_counts": task_counts,
            "recent_messages": recent_messages,
        },
    )


@router.get("/tasks", response_class=HTMLResponse)
async def dashboard_tasks(
    request: Request,
    chat_id: int | None = Query(default=None),
    status_filter: str | None = Query(default="open", alias="status"),
) -> HTMLResponse:
    # An empty string means "all statuses" (the <option value=""> in tasks.html) —
    # distinct from the default "open", and from search_tasks' own None-means-all.
    normalized_status = status_filter or None

    async with session_scope() as session:
        chat_ids = await messages_repo.list_chat_ids(session)
        selected = _selected_chat_id(chat_id, chat_ids)
        tasks = []
        if selected is not None:
            tasks = await tasks_repo.search_tasks(
                session, chat_id=selected, status=normalized_status, limit=200
            )

    return templates.TemplateResponse(
        request=request,
        name="tasks.html",
        context={
            "chat_ids": chat_ids,
            "selected_chat_id": selected,
            "tasks": tasks,
            "status_filter": status_filter,
            "statuses": TASK_STATUSES,
        },
    )


@router.post("/tasks/{task_id}/complete")
async def complete_task(task_id: int, chat_id: int = Query(...)) -> RedirectResponse:
    async with session_scope() as session:
        await tasks_repo.update_task(session, chat_id=chat_id, task_id=task_id, status="done")
    return RedirectResponse(url=f"/tasks?chat_id={chat_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, chat_id: int = Query(...)) -> RedirectResponse:
    async with session_scope() as session:
        await tasks_repo.update_task(session, chat_id=chat_id, task_id=task_id, status="cancelled")
    return RedirectResponse(url=f"/tasks?chat_id={chat_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/artifacts", response_class=HTMLResponse)
async def dashboard_artifacts(
    request: Request, chat_id: int | None = Query(default=None)
) -> HTMLResponse:
    async with session_scope() as session:
        chat_ids = await messages_repo.list_chat_ids(session)
        selected = _selected_chat_id(chat_id, chat_ids)
        artifacts = []
        if selected is not None:
            artifacts = await artifacts_repo.list_artifacts(session, chat_id=selected, limit=200)

    return templates.TemplateResponse(
        request=request,
        name="artifacts.html",
        context={"chat_ids": chat_ids, "selected_chat_id": selected, "artifacts": artifacts},
    )


@router.get("/artifacts/{name}/versions", response_class=HTMLResponse)
async def dashboard_artifact_versions(
    request: Request, name: str, chat_id: int = Query(...)
) -> HTMLResponse:
    async with session_scope() as session:
        chat_ids = await messages_repo.list_chat_ids(session)
        versions = await artifacts_repo.list_versions(session, chat_id=chat_id, name=name)

    return templates.TemplateResponse(
        request=request,
        name="artifact_versions.html",
        context={
            "chat_ids": chat_ids,
            "selected_chat_id": chat_id,
            "name": name,
            "chat_id": chat_id,
            "versions": versions,
        },
    )


@router.get("/artifacts/{name}/versions/{version}/download")
async def download_artifact_version(
    name: str, version: int, chat_id: int = Query(...)
) -> PlainTextResponse:
    async with session_scope() as session:
        found = await artifacts_repo.get_artifact_version(
            session, chat_id=chat_id, name=name, version=version
        )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    artifact, artifact_version = found
    filename = artifact_filename(artifact.name, artifact.language)
    return PlainTextResponse(
        artifact_version.code,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/usage", response_class=HTMLResponse)
async def dashboard_usage(
    request: Request,
    chat_id: int | None = Query(default=None),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    async with session_scope() as session:
        chat_ids = await messages_repo.list_chat_ids(session)
        selected = _selected_chat_id(chat_id, chat_ids)
        usage_rows = []
        if selected is not None:
            usage_rows = await messages_repo.get_daily_usage(
                session, chat_id=selected, days=settings.dashboard_usage_days
            )

    return templates.TemplateResponse(
        request=request,
        name="usage.html",
        context={
            "chat_ids": chat_ids,
            "selected_chat_id": selected,
            "usage_rows": usage_rows,
            "days": settings.dashboard_usage_days,
        },
    )


@router.get("/history", response_class=HTMLResponse)
async def dashboard_history(
    request: Request, chat_id: int | None = Query(default=None)
) -> HTMLResponse:
    async with session_scope() as session:
        chat_ids = await messages_repo.list_chat_ids(session)
        selected = _selected_chat_id(chat_id, chat_ids)
        history: list[dict[str, Any]] = []
        if selected is not None:
            rows = await messages_repo.get_recent_messages(session, chat_id=selected, limit=50)
            history = [_summarize_message(row) for row in rows]

    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={"chat_ids": chat_ids, "selected_chat_id": selected, "history": history},
    )
