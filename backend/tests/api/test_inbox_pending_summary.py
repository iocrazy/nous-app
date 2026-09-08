"""Phase 2a Task 8: GET /ai-library/inbox/pending-summary — per-issue count of
unclaimed steers, only for issues the caller can see (visibility folded into
the SQL, same predicate as the issue lists)."""

from __future__ import annotations

import datetime as dt
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

r = importlib.import_module("app.api.agent_inbox_router")
repo_mod = importlib.import_module("app.repositories.agent_run_inbox_repository")

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
T0 = dt.datetime(2026, 9, 8, 10, 0, tzinfo=dt.timezone.utc)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(r.router, prefix="/api/v1")
    from app.core.deps import get_auth

    async def _grant():
        return SimpleNamespace(user_id=ME)

    app.dependency_overrides[get_auth] = _grant
    return TestClient(app)


def test_pending_summary_stmt_folds_visibility_and_groups_by_target():
    sql = str(
        repo_mod.pending_summary_stmt(ME).compile(dialect=postgresql.dialect())
    ).lower()
    assert "from public.agent_run_inbox" in sql
    assert "target_kind = " in sql and "group by" in sql
    assert "claimed_at is null" in sql and "expired_at is null" in sql
    assert "count(" in sql and "min(" in sql
    # visibility: the issue must be one the caller can see, and not hidden
    assert "public.team_members" in sql and "hidden_at is null" in sql
    assert "target_id in (select" in sql


def test_endpoint_returns_string_ids_and_iso_times(monkeypatch):
    repo = SimpleNamespace(
        pending_summary=AsyncMock(
            return_value=[
                {"target_id": 310819108761499, "count": 2, "oldest_at": T0},
                {"target_id": 7, "count": 1, "oldest_at": T0},
            ]
        )
    )
    monkeypatch.setattr(r, "get_agent_run_inbox_repository", lambda: repo)
    resp = _client().get("/api/v1/ai-library/inbox/pending-summary?target_kind=issue")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(b["target_id"], b["count"]) for b in body] == [
        ("310819108761499", 2),
        ("7", 1),
    ]
    # timestamptz → ISO with an explicit offset (pydantic writes "Z")
    assert all(dt.datetime.fromisoformat(b["oldest_at"]) == T0 for b in body)
    repo.pending_summary.assert_awaited_once_with(ME)


def test_endpoint_only_supports_issue_targets(monkeypatch):
    monkeypatch.setattr(
        r,
        "get_agent_run_inbox_repository",
        lambda: SimpleNamespace(pending_summary=AsyncMock()),
    )
    resp = _client().get(
        "/api/v1/ai-library/inbox/pending-summary?target_kind=conversation"
    )
    assert resp.status_code == 422
