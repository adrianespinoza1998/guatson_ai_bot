"""Dashboard routes: auth, chat-scoped views, and the task-completion action.

Uses httpx's ASGI transport directly (see tests/test_webhook.py's docstring for why,
not fastapi.testclient.TestClient) so everything runs on the shared session event
loop alongside the process-wide DB engine singleton.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.dashboard.auth import get_app_settings
from app.db import get_session_factory, session_scope
from app.main import app
from app.repositories.artifacts import save_artifact_version
from app.repositories.messages import insert_assistant_message, insert_user_message
from app.repositories.tasks import create_task, get_task

pytestmark = pytest.mark.asyncio

DASHBOARD_PASSWORD = "test-dashboard-password"


async def _seed_chat(chat_id: int) -> None:
    """The chat selector is powered by `messages.chat_id` (no users table) — a chat
    that only has a task/artifact but no message wouldn't show up, which never
    happens for real (the agent always replies to a message first). Tests that hit a
    route depending on the selector need a message seeded to match that precondition.
    """
    async with session_scope() as session:
        await insert_user_message(
            session,
            chat_id=chat_id,
            telegram_update_id=None,
            content=[{"type": "text", "text": "seed"}],
            text_preview="seed",
        )


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


AUTH_HEADERS = _basic_auth_header("admin", DASHBOARD_PASSWORD)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    settings = get_settings().model_copy(update={"dashboard_password": DASHBOARD_PASSWORD})
    app.dependency_overrides[get_app_settings] = lambda: settings
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_without_password() -> AsyncIterator[AsyncClient]:
    settings = get_settings().model_copy(update={"dashboard_password": None})
    app.dependency_overrides[get_app_settings] = lambda: settings
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    app.dependency_overrides.clear()


async def test_no_credentials_returns_401(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


async def test_wrong_password_returns_401(client: AsyncClient) -> None:
    response = await client.get("/", headers=_basic_auth_header("admin", "wrong"))
    assert response.status_code == 401


async def test_dashboard_is_404_when_password_not_configured(
    client_without_password: AsyncClient,
) -> None:
    response = await client_without_password.get("/", headers=AUTH_HEADERS)
    assert response.status_code == 404


async def test_home_renders_with_correct_credentials(client: AsyncClient) -> None:
    chat_id = 7001
    await _seed_chat(chat_id)
    async with session_scope() as session:
        await create_task(session, chat_id=chat_id, title="Tarea de prueba dashboard")

    response = await client.get("/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert str(chat_id) in response.text


async def test_tasks_page_is_scoped_by_chat_id(client: AsyncClient) -> None:
    chat_a, chat_b = 7002, 7003
    await _seed_chat(chat_a)
    await _seed_chat(chat_b)
    async with session_scope() as session:
        await create_task(session, chat_id=chat_a, title="Tarea exclusiva de A")
        await create_task(session, chat_id=chat_b, title="Tarea exclusiva de B")

    response = await client.get(f"/tasks?chat_id={chat_a}&status=", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Tarea exclusiva de A" in response.text
    assert "Tarea exclusiva de B" not in response.text


async def test_complete_task_action_updates_status(client: AsyncClient) -> None:
    chat_id = 7004
    async with session_scope() as session:
        task = await create_task(session, chat_id=chat_id, title="Pagar hosting")
        task_id = task.id

    response = await client.post(
        f"/tasks/{task_id}/complete?chat_id={chat_id}", headers=AUTH_HEADERS
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/tasks?chat_id={chat_id}"

    factory = get_session_factory()
    async with factory() as session:
        updated = await get_task(session, chat_id=chat_id, task_id=task_id)
        assert updated is not None
        assert updated.status == "done"
        assert updated.completed_at is not None


async def test_download_artifact_version_returns_exact_code(client: AsyncClient) -> None:
    chat_id = 7005
    code = "print('hola dashboard')\n"
    async with session_scope() as session:
        await save_artifact_version(
            session, chat_id=chat_id, name="script", language="python", code=code
        )

    response = await client.get(
        f"/artifacts/script/versions/1/download?chat_id={chat_id}", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert response.text == code
    assert "attachment" in response.headers["content-disposition"]


async def test_usage_page_aggregates_tokens(client: AsyncClient) -> None:
    chat_id = 7006
    async with session_scope() as session:
        await insert_assistant_message(
            session,
            chat_id=chat_id,
            content=[{"type": "text", "text": "hola"}],
            text_preview="hola",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
            model="claude-sonnet-5",
        )

    response = await client.get(f"/usage?chat_id={chat_id}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "100" in response.text
    assert "50" in response.text
