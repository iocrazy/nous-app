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
# Repository (session boundary stubbed; real statement construction)
# ============================================================


class _CaptureResult:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _CaptureSession:
    def __init__(self, row=None) -> None:
        self.calls: list = []
        self._row = row

    async def execute(self, stmt, params=None):
        self.calls.append({"stmt": stmt, "params": params})
        return _CaptureResult(self._row)


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _CaptureSession:
    from contextlib import asynccontextmanager

    from app.repositories import project_style_profile_repository as mod

    session = _CaptureSession(row={"project_id": 777})

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(mod, "read_scope", _scope)
    monkeypatch.setattr(mod, "write_scope", _scope)
    return session


@pytest.mark.asyncio
async def test_get_returns_none_when_unsaved(
    fake_session: _CaptureSession, monkeypatch
) -> None:
    fake_session._row = None
    repo = ProjectStyleProfileRepository()
    assert await repo.get(777) is None
    stmt = fake_session.calls[0]["stmt"]
    # BIGINT column — the bind param must be an int.
    assert 777 in stmt.compile().params.values()
    assert "FROM public.project_style_profile" in str(stmt)


@pytest.mark.asyncio
async def test_get_for_storyboard_project_binds_int_and_joins(
    fake_session: _CaptureSession,
) -> None:
    fake_session._row = None
    repo = ProjectStyleProfileRepository()
    assert await repo.get_for_storyboard_project(888) is None
    call = fake_session.calls[0]
    assert 888 in call["stmt"].compile().params.values()
    assert "JOIN public.storyboard_projects" in str(call["stmt"])


@pytest.mark.asyncio
async def test_upsert_serializes_jsonb_params(fake_session: _CaptureSession) -> None:
    repo = ProjectStyleProfileRepository()
    await repo.upsert(
        777,
        style_md="noir look",
        visual_style={"palette": "muted"},
        reference_links=["res-1"],
        updated_by="00000000-0000-0000-0000-000000000001",
    )
    # The COALESCE-merge upsert keeps its SQL body (text()) — params travel
    # separately on the session.execute call.
    params = fake_session.calls[0]["params"]
    assert params["pid"] == 777
    assert params["style_md"] == "noir look"
    # jsonb travels as JSON text (CAST(:x AS JSONB) in the SQL).
    assert params["visual_style"] == '{"palette": "muted"}'
    assert params["reference_links"] == '["res-1"]'
    sql = str(fake_session.calls[0]["stmt"])
    assert "ON CONFLICT (project_id) DO UPDATE" in sql


@pytest.mark.asyncio
async def test_upsert_absent_fields_stay_none_for_coalesce_merge(
    fake_session: _CaptureSession,
) -> None:
    repo = ProjectStyleProfileRepository()
    await repo.upsert(777, style_md="only this")
    params = fake_session.calls[0]["params"]
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
