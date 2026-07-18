"""Unit tests for the Projects "Recent" view data path (feature/projects-recent-view).

Three surfaces:

1. ``ScriptProjectRepository.list_recent_for_user`` — recent non-deleted
   scripts across the caller's OWNED projects, newest-edited first. Joins
   ``projects`` for owner scoping + project name (no N+1). Asserts the WHERE
   / ORDER BY / LIMIT clauses are pushed to SQL and the row shape stringifies
   the bigint ids (the recent-items wire contract is string ids).

2. ``CanvasRepository.list_recent_for_user`` — same, for live canvases
   (``deleted_at IS NULL``).

3. ``ProjectsService.get_recent_items`` — merge-sort-cap: fetch both repos,
   tag each row with ``kind``, sort by ``updated_at`` desc, cap at ``limit``
   (clamped to 1..20).

Runs in the UNIT suite (no DSN): sessions + scopes are faked and the emitted
SQLAlchemy statement is captured + compiled, mirroring the capture-session
pattern from ``test_projects_list_filters.py``.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.canvas_repository as canvas_mod
import app.repositories.script_repository as script_mod
from app.repositories.canvas_repository import CanvasRepository
from app.repositories.script_repository import ScriptProjectRepository

pytestmark = pytest.mark.unit

_OWNER_ID = str(uuid.uuid4())
_PROJECT_ID = 900000000000001
_SCRIPT_ID = 900000000000002
_CANVAS_ID = 900000000000003
_TEAM_ID = 900000000000004


class _FakeMappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows if rows is not None else []

    def mappings(self):
        return _FakeMappings(self._rows)


class _CaptureSession:
    def __init__(self, rows=None):
        self.statements: list = []
        self._rows = rows

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self._rows)


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


# ── ScriptProjectRepository.list_recent_for_user ─────────────────────────


@pytest.mark.asyncio
async def test_script_recent_sql_join_scope_order_limit():
    session = _CaptureSession(rows=[])
    with patch.object(script_mod, "read_scope", lambda: _ScopeCtx(session)):
        await ScriptProjectRepository().list_recent_for_user(_OWNER_ID, limit=8)

    sql, _params = _rendered(session.statements[0])
    # Joins projects for owner scoping + project name + team_id (cross-team
    # navigation contract — the Recent view is owner-scoped across every
    # team the caller belongs to, so the wire shape must carry the item's
    # OWN team so the frontend can route to it, not the current page's team).
    assert "JOIN public.projects" in sql
    assert "public.projects.owner_id" in sql
    assert "public.projects.team_id" in sql
    # Excludes soft-deleted scripts.
    assert "public.script_projects.status !=" in sql
    # Newest-edited first, capped.
    assert "ORDER BY public.script_projects.updated_at DESC" in sql
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_script_recent_row_shape_stringifies_ids():
    row = {
        "id": _SCRIPT_ID,
        "name": "Episode 1",
        "project_id": _PROJECT_ID,
        "updated_at": _dt.datetime(2026, 7, 1, tzinfo=_dt.timezone.utc),
        "project_name": "My Show",
        "team_id": _TEAM_ID,
    }
    session = _CaptureSession(rows=[row])
    with patch.object(script_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await ScriptProjectRepository().list_recent_for_user(_OWNER_ID, limit=8)

    assert out == [
        {
            "id": str(_SCRIPT_ID),
            "name": "Episode 1",
            "project_id": str(_PROJECT_ID),
            "project_name": "My Show",
            "team_id": str(_TEAM_ID),
            "updated_at": "2026-07-01T00:00:00+00:00",
        }
    ]


@pytest.mark.asyncio
async def test_script_recent_row_shape_null_team_id_passes_through():
    """A personal project (``projects.team_id IS NULL``) must surface
    ``team_id: None`` — never coerced to the string ``"None"``."""
    row = {
        "id": _SCRIPT_ID,
        "name": "Episode 1",
        "project_id": _PROJECT_ID,
        "updated_at": _dt.datetime(2026, 7, 1, tzinfo=_dt.timezone.utc),
        "project_name": "My Show",
        "team_id": None,
    }
    session = _CaptureSession(rows=[row])
    with patch.object(script_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await ScriptProjectRepository().list_recent_for_user(_OWNER_ID, limit=8)

    assert out[0]["team_id"] is None


@pytest.mark.asyncio
async def test_script_recent_swallows_errors_returns_empty():
    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *exc):
            return False

    with patch.object(script_mod, "read_scope", lambda: _Boom()):
        out = await ScriptProjectRepository().list_recent_for_user(_OWNER_ID)
    assert out == []


# ── CanvasRepository.list_recent_for_user ────────────────────────────────


@pytest.mark.asyncio
async def test_canvas_recent_sql_join_scope_order_limit():
    session = _CaptureSession(rows=[])
    with patch.object(canvas_mod, "read_scope", lambda: _ScopeCtx(session)):
        await CanvasRepository().list_recent_for_user(_OWNER_ID, limit=8)

    sql, _params = _rendered(session.statements[0])
    assert "JOIN public.projects" in sql
    assert "public.projects.owner_id" in sql
    assert "public.projects.team_id" in sql
    # Live canvases only.
    assert "public.canvases.deleted_at IS NULL" in sql
    assert "ORDER BY public.canvases.updated_at DESC" in sql
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_canvas_recent_row_shape_stringifies_ids():
    row = {
        "id": _CANVAS_ID,
        "name": "Board A",
        "project_id": _PROJECT_ID,
        "updated_at": _dt.datetime(2026, 7, 2, tzinfo=_dt.timezone.utc),
        "project_name": "My Show",
        "team_id": _TEAM_ID,
    }
    session = _CaptureSession(rows=[row])
    with patch.object(canvas_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await CanvasRepository().list_recent_for_user(_OWNER_ID, limit=8)

    assert out == [
        {
            "id": str(_CANVAS_ID),
            "name": "Board A",
            "project_id": str(_PROJECT_ID),
            "project_name": "My Show",
            "team_id": str(_TEAM_ID),
            "updated_at": "2026-07-02T00:00:00+00:00",
        }
    ]


@pytest.mark.asyncio
async def test_canvas_recent_row_shape_null_team_id_passes_through():
    row = {
        "id": _CANVAS_ID,
        "name": "Board A",
        "project_id": _PROJECT_ID,
        "updated_at": _dt.datetime(2026, 7, 2, tzinfo=_dt.timezone.utc),
        "project_name": "My Show",
        "team_id": None,
    }
    session = _CaptureSession(rows=[row])
    with patch.object(canvas_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await CanvasRepository().list_recent_for_user(_OWNER_ID, limit=8)

    assert out[0]["team_id"] is None


# ── ProjectsService.get_recent_items — merge / sort / cap / kind ──────────


def _svc():
    from app.services.library.projects_service import ProjectsService

    return ProjectsService()


@pytest.mark.asyncio
async def test_recent_items_merges_sorts_tags_and_caps():
    scripts = [
        {
            "id": "1",
            "name": "S older",
            "project_id": "10",
            "project_name": "P",
            "team_id": "500",
            "updated_at": "2026-07-01T00:00:00+00:00",
        },
        {
            "id": "2",
            "name": "S newest",
            "project_id": "10",
            "project_name": "P",
            "team_id": "500",
            "updated_at": "2026-07-05T00:00:00+00:00",
        },
    ]
    canvases = [
        {
            "id": "3",
            "name": "C middle",
            "project_id": "20",
            "project_name": "Q",
            # A DIFFERENT team than the scripts above — the Recent view is
            # owner-scoped across every team the caller belongs to, so the
            # merge must pass each item's own team_id through untouched.
            "team_id": "777",
            "updated_at": "2026-07-03T00:00:00+00:00",
        },
    ]
    script_repo = MagicMock()
    script_repo.list_recent_for_user = AsyncMock(return_value=scripts)
    canvas_repo = MagicMock()
    canvas_repo.list_recent_for_user = AsyncMock(return_value=canvases)

    with (
        patch.object(
            script_mod, "get_script_project_repository", return_value=script_repo
        ),
        patch.object(canvas_mod, "CanvasRepository", return_value=canvas_repo),
    ):
        out = await _svc().get_recent_items(_OWNER_ID, limit=2)

    # Sorted by updated_at desc, capped at 2, tagged with kind.
    assert [i["id"] for i in out] == ["2", "3"]
    assert out[0]["kind"] == "script"
    assert out[1]["kind"] == "canvas"
    # team_id passes through the merge untouched — the frontend routes
    # cross-team items using this, not the current page's team.
    assert out[0]["team_id"] == "500"
    assert out[1]["team_id"] == "777"


@pytest.mark.asyncio
async def test_recent_items_clamps_limit_to_20():
    script_repo = MagicMock()
    script_repo.list_recent_for_user = AsyncMock(return_value=[])
    canvas_repo = MagicMock()
    canvas_repo.list_recent_for_user = AsyncMock(return_value=[])

    with (
        patch.object(
            script_mod, "get_script_project_repository", return_value=script_repo
        ),
        patch.object(canvas_mod, "CanvasRepository", return_value=canvas_repo),
    ):
        await _svc().get_recent_items(_OWNER_ID, limit=999)

    # Each repo is asked for at most the clamped ceiling (20).
    script_repo.list_recent_for_user.assert_awaited_once_with(_OWNER_ID, 20)
    canvas_repo.list_recent_for_user.assert_awaited_once_with(_OWNER_ID, 20)


# ── RecentItem schema — team_id field + Literal kind ─────────────────────


def test_recent_item_schema_accepts_team_id_and_null_team_id():
    from app.schemas.projects import RecentItem

    with_team = RecentItem(
        kind="script",
        id="1",
        name="Ep 1",
        project_id="10",
        project_name="P",
        team_id="500",
        updated_at="2026-07-01T00:00:00+00:00",
    )
    assert with_team.team_id == "500"

    personal = RecentItem(
        kind="canvas",
        id="2",
        name="Board",
        project_id="20",
        project_name="Q",
        team_id=None,
        updated_at=None,
    )
    assert personal.team_id is None


def test_recent_item_schema_rejects_invalid_kind():
    from pydantic import ValidationError

    from app.schemas.projects import RecentItem

    with pytest.raises(ValidationError):
        RecentItem(
            kind="bogus",
            id="1",
            name="Ep 1",
            project_id="10",
            project_name="P",
        )
