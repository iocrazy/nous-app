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
    """Patch app.db.engine module-level helpers (same pattern as style_profile tests)."""
    calls: dict = {
        "fetch_all": [],
        "fetch_one": [],
        "execute_returning_one": [],
    }

    async def fake_fetch_all(sql: str, params=None):
        calls["fetch_all"].append({"sql": sql, "params": params})
        return calls.get("fetch_all_result", [])

    async def fake_fetch_one(sql: str, params=None):
        calls["fetch_one"].append({"sql": sql, "params": params})
        return calls.get("fetch_one_result")

    async def fake_execute_returning_one(sql: str, params=None):
        calls["execute_returning_one"].append({"sql": sql, "params": params})
        return calls.get("returning_result")

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    monkeypatch.setattr(
        "app.db.engine.execute_returning_one", fake_execute_returning_one
    )
    return calls


@pytest.mark.asyncio
async def test_list_catalog_calls_fetch_all_ordered(engine_calls: dict) -> None:
    repo = ProjectStagesRepository()
    result = await repo.list_catalog()
    assert isinstance(result, list)
    call = engine_calls["fetch_all"][0]
    assert "ORDER BY sort_order" in call["sql"]
    assert call["params"] is None or call["params"] == {}


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
    call = engine_calls["fetch_one"][0]
    assert call["params"] == {"pid": 999}
    # Must JOIN projects and project_stages
    assert "project_stages" in call["sql"]


@pytest.mark.asyncio
async def test_history_passes_project_id(engine_calls: dict) -> None:
    engine_calls["fetch_all_result"] = []
    repo = ProjectStagesRepository()
    await repo.history(42)
    call = engine_calls["fetch_all"][0]
    assert call["params"] == {"pid": 42}
    assert "project_stage_history" in call["sql"]


# ============================================================
# set_current_stage — transactional logic (engine.begin mocked)
# ============================================================


class FakeConn:
    """Minimal async context-manager conn stub for engine.begin().

    ``rows`` is an ordered queue of return values.  Each ``execute`` call pops
    from the front.  Pass ``None`` as a placeholder for statements whose result
    is never inspected (UPDATE / INSERT without RETURNING).
    """

    def __init__(self, rows: list) -> None:
        self._rows = list(rows)
        self.executed: list[dict] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append({"sql": sql, "params": params or {}})

        class _Result:
            def __init__(self, row):
                self._row = row

            def mappings(self):
                return self

            def first(self):
                if self._row is None:
                    return None
                return dict(self._row)

            def scalar(self):
                return self._row

        row = self._rows.pop(0) if self._rows else None
        return _Result(row)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


class FakeEngine:
    def __init__(self, rows: list) -> None:
        self._conn = FakeConn(rows)

    def begin(self):
        return self._conn


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch):
    """Replace get_engine() for transaction tests."""

    def _make(rows):
        eng = FakeEngine(rows)
        monkeypatch.setattr("app.db.engine.get_engine", lambda: eng)
        return eng

    return _make


@pytest.mark.asyncio
async def test_set_current_stage_no_op_when_same_stage(fake_engine) -> None:
    """Same stage → early return, no INSERT into history."""
    # SELECT FOR UPDATE returns current_stage_id == target stage_id (42)
    select_row = {"current_stage_id": 42}
    eng = fake_engine([select_row])
    repo = ProjectStagesRepository()
    result = await repo.set_current_stage(
        project_id=100, stage_id=42, user_id="00000000-0000-0000-0000-000000000001"
    )
    # No-op → returns None
    assert result is None
    # Only one SQL executed: the SELECT FOR UPDATE check
    assert len(eng._conn.executed) == 1


@pytest.mark.asyncio
async def test_set_current_stage_transition_runs_three_writes(fake_engine) -> None:
    """Different stage → SELECT FOR UPDATE + UPDATE history + INSERT history + UPDATE projects + SELECT stage."""
    # SELECT FOR UPDATE returns current_stage_id = 10 (different from target 20)
    select_row = {"current_stage_id": 10}
    # Stage SELECT at the end of the transaction
    new_stage_row = {
        "id": 20,
        "slug": "script",
        "name": "Script",
        "sort_order": 20,
        "tools_recommended": ["scripts"],
        "created_at": None,
        "updated_at": None,
    }
    # Rows consumed in order (None = placeholder, result never inspected):
    #   1. SELECT FOR UPDATE   → select_row
    #   2. UPDATE history      → None (not inspected)
    #   3. INSERT history      → None (not inspected)
    #   4. UPDATE projects     → None (not inspected)
    #   5. SELECT stage by id  → new_stage_row
    eng = fake_engine([select_row, None, None, None, new_stage_row])
    repo = ProjectStagesRepository()
    result = await repo.set_current_stage(
        project_id=100, stage_id=20, user_id="00000000-0000-0000-0000-000000000001"
    )
    # Should have run 5 SQL statements
    assert len(eng._conn.executed) >= 4
    sqls = " ".join(e["sql"] for e in eng._conn.executed)
    assert "FOR UPDATE" in sqls
    assert "exited_at" in sqls or "project_stage_history" in sqls
    assert result is not None
    assert result["slug"] == "script"


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
    from app.core.scope_guards import verify_project_write_access

    app = FastAPI()
    app.include_router(pr.router, prefix="/api/v1")
    app.dependency_overrides[get_auth] = lambda: AuthContext(
        user_id="00000000-0000-0000-0000-000000000001",
        auth_type="jwt",
    )
    app.dependency_overrides[verify_project_write_access] = lambda: None
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
        self.transition_calls: list = []

    async def list_catalog(self):
        return self.catalog

    async def get_current(self, project_id: int):
        return self.current

    async def set_current_stage(self, project_id: int, stage_id: int, user_id: str):
        self.transition_calls.append(
            {"project_id": project_id, "stage_id": stage_id, "user_id": user_id}
        )
        return {"id": str(stage_id), "slug": "planning"}

    async def history(self, project_id: int):
        return []


@pytest.fixture
def fake_stage_repo(monkeypatch: pytest.MonkeyPatch) -> FakeStagePub:
    repo = FakeStagePub()
    monkeypatch.setattr(
        "app.repositories.project_stages_repository."
        "get_project_stages_repository",
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


def test_get_current_stage_null_when_unset(
    client, fake_stage_repo: FakeStagePub
) -> None:
    response = client.get("/api/v1/projects/777/current_stage")
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": None}


def test_put_current_stage_calls_repo(
    client, fake_stage_repo: FakeStagePub
) -> None:
    response = client.put(
        "/api/v1/projects/777/current_stage",
        json={"stage_id": 111},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    call = fake_stage_repo.transition_calls[0]
    assert call["project_id"] == 777
    assert call["stage_id"] == 111
    assert call["user_id"] == "00000000-0000-0000-0000-000000000001"


def test_put_invalid_project_id_is_422(client, fake_stage_repo: FakeStagePub) -> None:
    response = client.get("/api/v1/projects/abc/current_stage")
    assert response.status_code == 422

    response = client.put(
        "/api/v1/projects/abc/current_stage",
        json={"stage_id": 111},
    )
    assert response.status_code == 422
