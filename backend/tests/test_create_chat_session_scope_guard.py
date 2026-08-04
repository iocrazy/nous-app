"""Tests for the A2 review Critical fix: POST /agents/{slug}/sessions must
verify the caller can actually read ``project_id`` before it flows into
``conversations.project_id`` — and from there into every ``agent_runs`` row
(and bound ``AgentRunScope``) for that session.

Router-level tests driven through the real ASGI app, mirroring
``tests/test_parity_gap_coverage.py``'s harness (``get_auth`` dependency
override + ``AsyncClient``/``ASGITransport``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

_PROJECT_ID = 900200000000000001


async def _fake_auth():
    from app.core.deps import AuthContext

    return AuthContext(user_id=str(uuid4()), auth_type="jwt")


@pytest.fixture()
def _override_auth():
    from app.core.deps import get_auth
    from app.main import app

    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_session_rejects_project_the_caller_cannot_read(
    client: AsyncClient, _override_auth
) -> None:
    """The Critical bug this closes: project_id used to flow straight into
    conversations.project_id (and from there into agent_runs.project_id /
    the run's bound AgentRunScope) with NO ownership check at all. Now a
    project the caller can't read must 403 at session-creation time."""
    with (
        patch(
            "app.api.ai_library_router.verify_project_read_access",
            side_effect=HTTPException(
                status_code=403, detail="You do not have access to this project"
            ),
        ) as mock_guard,
        patch("app.api.ai_library_router.AILibraryChatService") as mock_svc_cls,
    ):
        resp = await client.post(
            "/api/v1/ai-library/agents/script_ai/sessions",
            json={"title": "hi", "project_id": _PROJECT_ID},
        )

    assert resp.status_code == 403
    mock_guard.assert_awaited_once()
    # The service layer must never be reached once the guard rejects.
    mock_svc_cls.return_value.create_session.assert_not_called()


@pytest.mark.asyncio
async def test_create_session_allows_when_ownership_verified(
    client: AsyncClient, _override_auth
) -> None:
    with (
        patch(
            "app.api.ai_library_router.verify_project_read_access",
            new=AsyncMock(return_value=None),
        ) as mock_guard,
        patch("app.api.ai_library_router.AILibraryChatService") as mock_svc_cls,
    ):
        mock_svc_cls.return_value.create_session = AsyncMock(
            return_value={
                "id": "1",
                "user_id": str(uuid4()),
                "agent_id": str(uuid4()),
                "agent_slug": "script_ai",
                "title": "hi",
                "project_id": _PROJECT_ID,
                "team_id": None,
                "created_at": "2026-08-04T00:00:00Z",
                "updated_at": "2026-08-04T00:00:00Z",
            }
        )
        resp = await client.post(
            "/api/v1/ai-library/agents/script_ai/sessions",
            json={"title": "hi", "project_id": _PROJECT_ID},
        )

    assert resp.status_code == 201
    mock_guard.assert_awaited_once()
    mock_svc_cls.return_value.create_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_session_skips_ownership_check_when_no_project_id(
    client: AsyncClient, _override_auth
) -> None:
    """No project_id means nothing to own — the guard must not even be
    invoked, and the session creates exactly as it always did for
    personal-scope (no project) sessions."""
    with (
        patch("app.api.ai_library_router.verify_project_read_access") as mock_guard,
        patch("app.api.ai_library_router.AILibraryChatService") as mock_svc_cls,
    ):
        mock_svc_cls.return_value.create_session = AsyncMock(
            return_value={
                "id": "1",
                "user_id": str(uuid4()),
                "agent_id": str(uuid4()),
                "agent_slug": "script_ai",
                "title": "hi",
                "project_id": None,
                "team_id": None,
                "created_at": "2026-08-04T00:00:00Z",
                "updated_at": "2026-08-04T00:00:00Z",
            }
        )
        resp = await client.post(
            "/api/v1/ai-library/agents/script_ai/sessions",
            json={"title": "hi"},
        )

    assert resp.status_code == 201
    mock_guard.assert_not_called()
    mock_svc_cls.return_value.create_session.assert_awaited_once()
