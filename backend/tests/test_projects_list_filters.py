"""Unit tests for Task 7 (PR-A2): real archive support + SQL filter pushdown +
N+1 fix for the projects list.

Three bugs fixed here:

1. ``get_user_projects`` never filtered by ``archived_at`` / ``is_starred`` /
   ``project_type`` in SQL — the router applied ``starred`` / ``project_type``
   in-memory AFTER fetching, and there was no way to exclude archived
   projects at all (``archived_at`` didn't exist as a filter). These tests
   assert the WHERE clause is built in SQL for all three filters.
2. ``get_projects_with_counts`` computed the per-project file count with
   ``asyncio.gather`` over N calls to ``get_project_file_count`` — one query
   per project (classic N+1). ``get_project_file_counts`` replaces this with
   ONE ``GROUP BY`` query.
3. ``update_project`` had no way to archive/unarchive a project — the
   service now maps ``ProjectUpdate.archived`` (bool) to a real
   ``archived_at`` timestamp/``None`` before it reaches the repo.

These run in the UNIT suite (no DSN): the session + scopes are faked and the
emitted SQLAlchemy statement is captured and compiled, mirroring the
capture-session pattern from ``test_projects_member_update_delete.py`` /
``test_project_file_comments.py``.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.projects_repository as orm_mod
from app.models.teams import Projects
from app.repositories.projects_repository import ProjectsRepository

pytestmark = pytest.mark.unit

_OWNER_ID = str(uuid.uuid4())
_PROJECT_ID_1 = 111111111
_PROJECT_ID_2 = 222222222


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    """Supports both ``.scalars().all()`` (ORM object rows) and ``.all()``
    (tuple rows, for the GROUP BY count query)."""

    def __init__(self, rows):
        self._rows = rows if rows is not None else []

    def scalars(self):
        return _FakeScalars(self._rows)

    def all(self):
        return self._rows


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


def _project_row(**overrides) -> Projects:
    defaults = dict(
        id=_PROJECT_ID_1,
        name="Test Project",
        owner_id=uuid.UUID(_OWNER_ID),
        project_type="personal",
        is_starred=False,
        team_id=None,
        description=None,
        project_group=None,
        workflow_id=None,
        announcement=None,
        color_label=None,
        archived_at=None,
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        visibility="inherited",
    )
    defaults.update(overrides)
    return Projects(**defaults)


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


# ── get_user_projects: archived filter pushdown ──────────────────────────


@pytest.mark.asyncio
async def test_get_user_projects_archived_false_filters_null_archived_at():
    """Default (archived=False, matching the current router default):
    WHERE projects.archived_at IS NULL."""
    session = _CaptureSession(rows=[_project_row()])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        await ProjectsRepository().get_user_projects(_OWNER_ID, archived=False)

    sql, _params = _rendered(session.statements[0])
    assert "projects.archived_at IS NULL" in sql


@pytest.mark.asyncio
async def test_get_user_projects_archived_true_filters_not_null_archived_at():
    """archived=True: WHERE projects.archived_at IS NOT NULL."""
    session = _CaptureSession(rows=[])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        await ProjectsRepository().get_user_projects(_OWNER_ID, archived=True)

    sql, _params = _rendered(session.statements[0])
    assert "projects.archived_at IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_get_user_projects_archived_none_omits_archived_filter():
    """archived=None: no archived_at filter at all — both archived and
    active projects return (the router's omitted-param case)."""
    session = _CaptureSession(rows=[])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        await ProjectsRepository().get_user_projects(_OWNER_ID, archived=None)

    sql, _params = _rendered(session.statements[0])
    # archived_at is always in the SELECT *-shaped column list (it's a real
    # column) — what must be ABSENT is a WHERE-clause filter on it.
    assert "archived_at IS NULL" not in sql
    assert "archived_at IS NOT NULL" not in sql


# ── get_user_projects: starred / project_type filter pushdown ────────────


@pytest.mark.asyncio
async def test_get_user_projects_starred_filter_pushed_to_sql():
    session = _CaptureSession(rows=[])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        await ProjectsRepository().get_user_projects(
            _OWNER_ID, starred=True, archived=None
        )

    sql, _params = _rendered(session.statements[0])
    # SQLAlchemy renders ``.is_(True)`` as a literal boolean comparison
    # (``IS true``), not a bound parameter.
    assert "projects.is_starred IS true" in sql


@pytest.mark.asyncio
async def test_get_user_projects_project_type_filter_pushed_to_sql():
    session = _CaptureSession(rows=[])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        await ProjectsRepository().get_user_projects(
            _OWNER_ID, project_type="external", archived=None
        )

    sql, params = _rendered(session.statements[0])
    assert "projects.project_type" in sql
    assert "external" in params.values()


@pytest.mark.asyncio
async def test_get_user_projects_returns_archived_at_in_row():
    """The returned row dict carries archived_at (ISO str when set, per the
    Strategy-C datetime parity rule)."""
    archived_row = _project_row(
        id=_PROJECT_ID_2,
        archived_at=_dt.datetime(2026, 7, 1, tzinfo=_dt.timezone.utc),
    )
    session = _CaptureSession(rows=[archived_row])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await ProjectsRepository().get_user_projects(_OWNER_ID, archived=True)

    assert out[0]["archived_at"] == "2026-07-01T00:00:00+00:00"


# ── get_project_file_counts: batched N+1 fix ──────────────────────────────


@pytest.mark.asyncio
async def test_get_project_file_counts_single_group_by_query():
    """ONE query with a GROUP BY — not one query per project (the N+1 this
    replaces)."""
    session = _CaptureSession(rows=[(_PROJECT_ID_1, 3), (_PROJECT_ID_2, 0)])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        counts = await ProjectsRepository().get_project_file_counts(
            [str(_PROJECT_ID_1), str(_PROJECT_ID_2)]
        )

    assert len(session.statements) == 1
    sql, params = _rendered(session.statements[0])
    assert "GROUP BY" in sql
    assert "project_files.is_trashed" in sql
    # The IN-list is bound as a single expanding param (a list value).
    bound_ids = params["project_id_1"]
    assert _PROJECT_ID_1 in bound_ids
    assert _PROJECT_ID_2 in bound_ids
    assert counts == {str(_PROJECT_ID_1): 3, str(_PROJECT_ID_2): 0}


@pytest.mark.asyncio
async def test_get_project_file_counts_empty_list_short_circuits():
    """No project ids → no query at all."""
    session = _CaptureSession(rows=[])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        counts = await ProjectsRepository().get_project_file_counts([])

    assert counts == {}
    assert len(session.statements) == 0


# ── service: get_projects_with_counts batches counts, no N+1 ─────────────


@pytest.mark.asyncio
async def test_service_get_projects_with_counts_uses_batched_counts():
    """The service must call ``get_project_file_counts`` ONCE with the full
    id list — never the old per-project ``get_project_file_count`` loop."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    projects = [
        {"id": _PROJECT_ID_1, "name": "A"},
        {"id": _PROJECT_ID_2, "name": "B"},
    ]
    with (
        patch.object(
            svc.repo, "get_user_projects", new=AsyncMock(return_value=projects)
        ),
        patch.object(
            svc.repo,
            "get_project_file_counts",
            new=AsyncMock(return_value={str(_PROJECT_ID_1): 5, str(_PROJECT_ID_2): 0}),
        ) as batched_mock,
        patch.object(svc.repo, "get_project_file_count", new=AsyncMock()) as n1_mock,
    ):
        out = await svc.get_projects_with_counts(_OWNER_ID)

    batched_mock.assert_awaited_once_with([_PROJECT_ID_1, _PROJECT_ID_2])
    n1_mock.assert_not_awaited()
    assert out[0]["file_count"] == 5
    assert out[1]["file_count"] == 0


@pytest.mark.asyncio
async def test_service_get_projects_with_counts_passthrough_filters():
    """team_id / project_type / starred / archived all pass straight through
    to the repo — no in-memory filtering left in the service."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo, "get_user_projects", new=AsyncMock(return_value=[])
        ) as get_mock,
        patch.object(svc.repo, "get_project_file_counts", new=AsyncMock()),
    ):
        out = await svc.get_projects_with_counts(
            _OWNER_ID,
            team_id="42",
            project_type="internal",
            starred=True,
            archived=None,
        )

    get_mock.assert_awaited_once_with(
        _OWNER_ID,
        team_id="42",
        project_type="internal",
        starred=True,
        archived=None,
    )
    assert out == []


# ── service: update_project maps archived → archived_at ──────────────────


@pytest.mark.asyncio
async def test_service_update_project_maps_archived_true_to_timestamp():
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo,
            "get_project_by_id",
            new=AsyncMock(return_value={"id": _PROJECT_ID_1}),
        ),
        patch.object(
            svc.repo,
            "update_project",
            new=AsyncMock(return_value={"id": _PROJECT_ID_1}),
        ) as update_mock,
    ):
        await svc.update_project(str(_PROJECT_ID_1), _OWNER_ID, {"archived": True})

    sent = update_mock.call_args.args[1]
    assert "archived" not in sent
    assert isinstance(sent["archived_at"], _dt.datetime)
    assert sent["archived_at"].tzinfo is not None


@pytest.mark.asyncio
async def test_service_update_project_maps_archived_false_to_none():
    """Unarchive: archived=False must reach the repo as archived_at=None —
    this only works if the router/schema treat ``archived`` as a tri-state
    Optional[bool] and exclude_none (not exclude_unset) at the boundary."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo,
            "get_project_by_id",
            new=AsyncMock(return_value={"id": _PROJECT_ID_1}),
        ),
        patch.object(
            svc.repo,
            "update_project",
            new=AsyncMock(return_value={"id": _PROJECT_ID_1}),
        ) as update_mock,
    ):
        await svc.update_project(str(_PROJECT_ID_1), _OWNER_ID, {"archived": False})

    sent = update_mock.call_args.args[1]
    assert "archived" not in sent
    assert sent["archived_at"] is None


@pytest.mark.asyncio
async def test_service_update_project_no_archived_key_passes_through_unchanged():
    """A plain field update (no ``archived`` key) must not touch archived_at
    at all."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo,
            "get_project_by_id",
            new=AsyncMock(return_value={"id": _PROJECT_ID_1}),
        ),
        patch.object(
            svc.repo,
            "update_project",
            new=AsyncMock(return_value={"id": _PROJECT_ID_1}),
        ) as update_mock,
    ):
        await svc.update_project(str(_PROJECT_ID_1), _OWNER_ID, {"name": "Renamed"})

    sent = update_mock.call_args.args[1]
    assert "archived_at" not in sent
    assert sent["name"] == "Renamed"


# ── create_project: team_id str → int coercion (asyncpg int8 strict) ─────


@pytest.mark.asyncio
async def test_create_project_coerces_str_team_id_to_int():
    """The request schema types ``team_id`` as ``str`` but the column is
    BIGINT and asyncpg's int8 codec rejects strings (DataError → 500, hit
    live 2026-07-06: team projects could not be created via the API at all).
    The repo must coerce before the INSERT, mirroring ``list_projects``."""
    session = _CaptureSession(rows=[_project_row(team_id=324864736396535)])
    repo = ProjectsRepository()
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        await repo.create_project(
            {
                "name": "Team Project",
                "owner_id": _OWNER_ID,
                "project_type": "internal",
                "team_id": "324864736396535",
            }
        )
    assert len(session.statements) == 1
    _, params = _rendered(session.statements[0])
    assert params["team_id"] == 324864736396535
    assert isinstance(params["team_id"], int)


@pytest.mark.asyncio
async def test_create_project_none_team_id_stays_none():
    session = _CaptureSession(rows=[_project_row()])
    repo = ProjectsRepository()
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        await repo.create_project(
            {"name": "Solo Project", "owner_id": _OWNER_ID, "team_id": None}
        )
    _, params = _rendered(session.statements[0])
    assert params.get("team_id") is None
