"""Tests for project_sop_stages repo + endpoints (Phase 5b).

TDD: tests are written before the implementation.  Run with:
    cd backend && uv run pytest tests/test_project_sop_stages.py -q
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.repositories.project_stages_repository import (
    ProjectStagesRepository,
    _serialize,
)

# ============================================================
# _serialize
# ============================================================


class TestSerialize:
    def test_timestamps_iso_and_bigint_str(self) -> None:
        when = datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc)
        out = _serialize(
            {
                "id": 123456789,
                "slug": "planning",
                "name": "Planning",
                "sort_order": 10,
                "tools_recommended": ["files", "tasks"],
                "created_at": when,
                "updated_at": when,
            }
        )
        assert out["created_at"] == "2026-06-14T09:00:00+00:00"
        assert out["updated_at"] == "2026-06-14T09:00:00+00:00"
        # BIGINT ids stay as str in REST shape
        assert out["id"] == "123456789"

    def test_bigint_id_already_str_is_left_alone(self) -> None:
        out = _serialize({"id": "9876543210", "other": "x"})
        assert out["id"] == "9876543210"

    def test_tools_recommended_jsonb_text_parsed(self) -> None:
        out = _serialize({"tools_recommended": '["files","tasks"]'})
        assert out["tools_recommended"] == ["files", "tasks"]

    def test_tools_recommended_list_is_kept(self) -> None:
        out = _serialize({"tools_recommended": ["storyboard"]})
        assert out["tools_recommended"] == ["storyboard"]

    def test_none_tools_recommended_becomes_empty_list(self) -> None:
        out = _serialize({"tools_recommended": None})
        assert out["tools_recommended"] == []


# ============================================================
# Repository — engine helpers mocked
# ============================================================


@pytest.fixture
def engine_calls(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stub the read_scope() session (the repo runs on the ORM session scopes
    now); capture every statement + params. `fetch_all_result` /
    `fetch_one_result` seed the canned rows, mirroring the old fixture."""
    from contextlib import asynccontextmanager

    calls: dict = {"executed": []}

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return list(calls.get("fetch_all_result", []))

        def first(self):
            return calls.get("fetch_one_result")

    class _Session:
        async def execute(self, stmt, params=None):
            calls["executed"].append({"stmt": stmt, "params": params})
            return _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.db.session.read_scope", _scope)
    return calls


@pytest.mark.asyncio
async def test_list_catalog_calls_fetch_all_ordered(engine_calls: dict) -> None:
    repo = ProjectStagesRepository()
    result = await repo.list_catalog()
    assert isinstance(result, list)
    stmt = engine_calls["executed"][0]["stmt"]
    assert "ORDER BY" in str(stmt) and "sort_order" in str(stmt)
    assert stmt.compile().params == {}


@pytest.mark.asyncio
async def test_list_catalog_serializes_rows(engine_calls: dict) -> None:
    engine_calls["fetch_all_result"] = [
        {
            "id": 111,
            "slug": "planning",
            "name": "Planning",
            "sort_order": 10,
            "tools_recommended": ["files"],
            "created_at": None,
            "updated_at": None,
        }
    ]
    repo = ProjectStagesRepository()
    result = await repo.list_catalog()
    assert result[0]["id"] == "111"
    assert result[0]["tools_recommended"] == ["files"]


@pytest.mark.asyncio
async def test_get_current_returns_none_when_no_stage(engine_calls: dict) -> None:
    repo = ProjectStagesRepository()
    result = await repo.get_current(999)
    assert result is None
    call = engine_calls["executed"][0]
    assert call["params"] == {"pid": 999}
    # Must JOIN projects and project_stages
    assert "project_stages" in str(call["stmt"])


@pytest.mark.asyncio
async def test_history_passes_project_id(engine_calls: dict) -> None:
    engine_calls["fetch_all_result"] = []
    repo = ProjectStagesRepository()
    await repo.history(42)
    call = engine_calls["executed"][0]
    assert call["params"] == {"pid": 42}
    assert "project_stage_history" in str(call["stmt"])


# ============================================================
# set_current_stage was retired in M2 PR-G (current_stage_id end-to-end
# retirement) — the transactional tests that used to live here are gone
# along with the method. See task-G1-report.md.
# ============================================================


# ============================================================
# Endpoints (guard + repo mocked)
# ============================================================


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    pr = importlib.import_module("app.api.projects_router")
    from app.core.deps import AuthContext, get_auth
    from app.core.scope_guards import (
        verify_project_read_access,
        verify_project_write_access,
    )

    app = FastAPI()
    app.include_router(pr.router, prefix="/api/v1")
    app.dependency_overrides[get_auth] = lambda: AuthContext(
        user_id="00000000-0000-0000-0000-000000000001",
        auth_type="jwt",
    )
    app.dependency_overrides[verify_project_write_access] = lambda: None
    app.dependency_overrides[verify_project_read_access] = lambda: None
    return TestClient(app)


class FakeStagePub:
    """Fake repo returned from the singleton getter."""

    def __init__(self) -> None:
        self.catalog = [
            {
                "id": "111",
                "slug": "planning",
                "name": "Planning",
                "sort_order": 10,
                "tools_recommended": ["files"],
            }
        ]
        self.current: dict | None = None

    async def list_catalog(self):
        return self.catalog

    async def get_current(self, project_id: int):
        return self.current

    async def history(self, project_id: int):
        return []


@pytest.fixture
def fake_stage_repo(monkeypatch: pytest.MonkeyPatch) -> FakeStagePub:
    repo = FakeStagePub()
    monkeypatch.setattr(
        "app.repositories.project_stages_repository." "get_project_stages_repository",
        lambda: repo,
    )
    return repo


def test_catalog_endpoint_returns_list(client, fake_stage_repo: FakeStagePub) -> None:
    response = client.get("/api/v1/projects/stages/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert body["data"][0]["slug"] == "planning"
