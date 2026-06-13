"""Tests for project_style_profile repo + endpoints (Phase 4 M8)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.repositories.project_style_profile_repository import (
    ProjectStyleProfileRepository,
    _serialize,
)

# ============================================================
# _serialize
# ============================================================


class TestSerialize:
    def test_timestamps_iso_and_uuid_str(self) -> None:
        import uuid

        when = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
        uid = uuid.uuid4()
        out = _serialize(
            {
                "project_id": 777,
                "style_md": "noir",
                "visual_style": {"palette": "muted"},
                "reference_links": [],
                "updated_by": uid,
                "created_at": when,
                "updated_at": when,
            }
        )
        assert out["created_at"] == "2026-06-11T12:00:00+00:00"
        assert out["updated_by"] == str(uid)
        assert out["project_id"] == 777  # bigint stays native int

    def test_jsonb_text_fallback_is_parsed(self) -> None:
        out = _serialize(
            {
                "visual_style": '{"palette": "muted"}',
                "reference_links": "[1, 2]",
                "updated_by": None,
            }
        )
        assert out["visual_style"] == {"palette": "muted"}
        assert out["reference_links"] == [1, 2]
        assert out["updated_by"] is None


# ============================================================
# Repository (engine helpers mocked)
# ============================================================


@pytest.fixture
def engine_calls(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"fetch_one": [], "execute_returning_one": []}

    async def fake_fetch_one(sql: str, params=None):
        calls["fetch_one"].append({"sql": sql, "params": params})
        return calls.get("fetch_one_result")

    async def fake_execute_returning_one(sql: str, params=None):
        calls["execute_returning_one"].append({"sql": sql, "params": params})
        return calls.get("returning_result", {"project_id": params["pid"]})

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    monkeypatch.setattr(
        "app.db.engine.execute_returning_one", fake_execute_returning_one
    )
    return calls


@pytest.mark.asyncio
async def test_get_returns_none_when_unsaved(engine_calls: dict) -> None:
    repo = ProjectStyleProfileRepository()
    assert await repo.get(777) is None
    # BIGINT column — the bind param must be an int.
    assert engine_calls["fetch_one"][0]["params"] == {"pid": 777}


@pytest.mark.asyncio
async def test_get_for_storyboard_project_binds_int_and_joins(
    engine_calls: dict,
) -> None:
    repo = ProjectStyleProfileRepository()
    assert await repo.get_for_storyboard_project(888) is None
    call = engine_calls["fetch_one"][0]
    assert call["params"] == {"sbid": 888}
    assert "JOIN public.storyboard_projects" in call["sql"]


@pytest.mark.asyncio
async def test_upsert_serializes_jsonb_params(engine_calls: dict) -> None:
    repo = ProjectStyleProfileRepository()
    await repo.upsert(
        777,
        style_md="noir look",
        visual_style={"palette": "muted"},
        reference_links=["res-1"],
        updated_by="00000000-0000-0000-0000-000000000001",
    )
    params = engine_calls["execute_returning_one"][0]["params"]
    assert params["pid"] == 777
    assert params["style_md"] == "noir look"
    # jsonb travels as JSON text (CAST(:x AS JSONB) in the SQL).
    assert params["visual_style"] == '{"palette": "muted"}'
    assert params["reference_links"] == '["res-1"]'


@pytest.mark.asyncio
async def test_upsert_absent_fields_stay_none_for_coalesce_merge(
    engine_calls: dict,
) -> None:
    repo = ProjectStyleProfileRepository()
    await repo.upsert(777, style_md="only this")
    params = engine_calls["execute_returning_one"][0]["params"]
    # None binds → COALESCE keeps the stored value (never clobber).
    assert params["visual_style"] is None
    assert params["reference_links"] is None


# ============================================================
# Endpoints (guard + repo mocked)
# ============================================================


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # app.api/__init__ re-exports the APIRouter under the module's name,
    # so a plain `from app.api import projects_router` grabs the router.
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


class FakeRepo:
    def __init__(self) -> None:
        self.saved: list[dict] = []
        self.profile = None

    async def get(self, project_id: int):
        return self.profile

    async def upsert(self, project_id: int, **fields):
        self.saved.append({"project_id": project_id, **fields})
        return {"project_id": project_id, **fields}


@pytest.fixture
def fake_repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    repo = FakeRepo()
    monkeypatch.setattr(
        "app.repositories.project_style_profile_repository."
        "get_project_style_profile_repository",
        lambda: repo,
    )
    return repo


def test_get_endpoint_null_when_unsaved(client, fake_repo: FakeRepo) -> None:
    response = client.get("/api/v1/projects/777/style-profile")
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": None}


def test_put_endpoint_merges_and_stamps_updated_by(client, fake_repo: FakeRepo) -> None:
    response = client.put(
        "/api/v1/projects/777/style-profile",
        json={"style_md": "noir", "visual_style": {"palette": "muted"}},
    )
    assert response.status_code == 200
    saved = fake_repo.saved[0]
    assert saved["project_id"] == 777
    assert saved["style_md"] == "noir"
    assert saved["visual_style"] == {"palette": "muted"}
    assert saved["reference_links"] is None  # absent → merge keeps stored
    assert saved["updated_by"] == "00000000-0000-0000-0000-000000000001"


def test_non_numeric_project_id_is_422(client, fake_repo: FakeRepo) -> None:
    assert client.get("/api/v1/projects/abc/style-profile").status_code == 422
    response = client.put("/api/v1/projects/abc/style-profile", json={"style_md": "x"})
    assert response.status_code == 422
