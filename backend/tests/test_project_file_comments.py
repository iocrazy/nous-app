"""Unit tests for the project_file_comments repository methods (PR-A2, Task 5).

These four methods (``get_comments_for_file`` / ``create_comment`` /
``get_comment_by_id`` / ``delete_comment``) were 500-ing in production since
migration 062 dropped the 043-era ``review_comments`` columns they targeted
(``file_id`` / ``timestamp_seconds`` / ``drawing_data``). Migration 335
(Task 4, committed 28fa21c4) created a dedicated ``project_file_comments``
table + the ``ProjectFileComments`` ORM model — the deferred product decision
is now RESOLVED (dedicated table, not the resources review system), so this
rewrites the four repo methods onto it via the SQLAlchemy session layer.

These run in the UNIT suite (no DSN): the session + scopes are faked, and the
emitted SQLAlchemy statement is captured and compiled to prove the query
targets ``project_file_comments`` and filters by ``version_id`` when given.
Mirrors the fixture-free capture-session pattern from
``test_projects_member_update_delete.py`` (PR #999) — no ``fake_read_session``
/ ``fake_write_session`` fixtures exist in this repo.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.projects_repository as orm_mod
from app.models.teams import ProjectFileComments
from app.repositories.projects_repository import ProjectsRepository

_AUTHOR_ID = str(uuid.uuid4())
_FILE_ID = 111222333
_VERSION_ID = 444555666
_COMMENT_ID = 777888999


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        if isinstance(rows, list):
            self._rows = rows
        elif rows is None:
            self._rows = []
        else:
            self._rows = [rows]

    def scalars(self):
        return _FakeScalars(self._rows)


class _CaptureSession:
    """Records every statement passed to ``execute`` and returns fixed rows."""

    def __init__(self, rows=None, raise_on_execute=None):
        self.statements: list = []
        self._rows = rows
        self._raise = raise_on_execute

    async def execute(self, stmt):
        self.statements.append(stmt)
        if self._raise is not None:
            raise self._raise
        return _FakeResult(self._rows)


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _comment_row(**overrides) -> ProjectFileComments:
    defaults = dict(
        id=_COMMENT_ID,
        file_id=_FILE_ID,
        version_id=_VERSION_ID,
        author_id=uuid.UUID(_AUTHOR_ID),
        content="Looks good",
        timestamp_seconds=12.5,
        drawing_data=None,
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
    )
    defaults.update(overrides)
    return ProjectFileComments(**defaults)


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


# ── get_comments_for_file ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_comments_targets_new_table_and_filters_version():
    session = _CaptureSession(rows=[_comment_row()])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await ProjectsRepository().get_comments_for_file(
            str(_FILE_ID), version_id=str(_VERSION_ID)
        )

    assert len(session.statements) == 1
    sql, params = _rendered(session.statements[0])
    assert "project_file_comments" in sql
    # Appears once in the SELECT column list, twice when the version_id
    # filter is also applied in the WHERE clause.
    assert sql.count("project_file_comments.version_id") == 2
    assert _FILE_ID in params.values()
    assert _VERSION_ID in params.values()
    assert len(out) == 1
    assert out[0]["id"] == str(_COMMENT_ID)
    assert out[0]["file_id"] == str(_FILE_ID)
    assert out[0]["version_id"] == str(_VERSION_ID)
    assert out[0]["author_id"] == _AUTHOR_ID


@pytest.mark.asyncio
async def test_get_comments_without_version_id_omits_filter():
    session = _CaptureSession(rows=[])
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await ProjectsRepository().get_comments_for_file(str(_FILE_ID))

    sql, _params = _rendered(session.statements[0])
    # Only the SELECT column list mention — no WHERE version_id filter.
    assert sql.count("project_file_comments.version_id") == 1
    assert out == []


@pytest.mark.asyncio
async def test_get_comments_raises_on_db_error():
    """Spec fix: previously swallowed the exception and returned ``[]`` —
    now raises so the router's existing 500 handler surfaces the real
    failure instead of masking it as an empty comment list."""
    session = _CaptureSession(raise_on_execute=RuntimeError("boom"))
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        with pytest.raises(RuntimeError):
            await ProjectsRepository().get_comments_for_file(str(_FILE_ID))


# ── create_comment ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_comment_returns_str_ids():
    session = _CaptureSession(rows=_comment_row())
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ProjectsRepository().create_comment(
            {
                "file_id": str(_FILE_ID),
                "author_id": _AUTHOR_ID,
                "content": "Looks good",
                "timestamp_seconds": 12.5,
            }
        )

    sql, _params = _rendered(session.statements[0])
    assert "project_file_comments" in sql
    assert isinstance(out.get("id"), str)
    assert out.get("file_id") == str(_FILE_ID)
    assert out.get("author_id") == _AUTHOR_ID


@pytest.mark.asyncio
async def test_create_comment_coerces_file_id_and_version_id_for_write():
    session = _CaptureSession(rows=_comment_row())
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ProjectsRepository().create_comment(
            {
                "file_id": str(_FILE_ID),
                "version_id": str(_VERSION_ID),
                "author_id": _AUTHOR_ID,
                "content": "x",
            }
        )
    _sql, params = _rendered(session.statements[0])
    assert _FILE_ID in params.values()
    assert _VERSION_ID in params.values()


@pytest.mark.asyncio
async def test_create_comment_raises_on_db_error():
    session = _CaptureSession(raise_on_execute=RuntimeError("boom"))
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        with pytest.raises(RuntimeError):
            await ProjectsRepository().create_comment(
                {"file_id": str(_FILE_ID), "author_id": _AUTHOR_ID, "content": "x"}
            )


# ── get_comment_by_id ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_comment_by_id_found():
    session = _CaptureSession(rows=_comment_row())
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await ProjectsRepository().get_comment_by_id(str(_COMMENT_ID))

    sql, params = _rendered(session.statements[0])
    assert "project_file_comments" in sql
    assert _COMMENT_ID in params.values()
    assert out["id"] == str(_COMMENT_ID)
    assert out["author_id"] == _AUTHOR_ID


@pytest.mark.asyncio
async def test_get_comment_by_id_not_found_returns_none():
    session = _CaptureSession(rows=None)
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await ProjectsRepository().get_comment_by_id(str(_COMMENT_ID))
    assert out is None


@pytest.mark.asyncio
async def test_get_comment_by_id_raises_on_db_error():
    session = _CaptureSession(raise_on_execute=RuntimeError("boom"))
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        with pytest.raises(RuntimeError):
            await ProjectsRepository().get_comment_by_id(str(_COMMENT_ID))


# ── delete_comment ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_comment_targets_new_table():
    session = _CaptureSession(rows=[])
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        ok = await ProjectsRepository().delete_comment(str(_COMMENT_ID))

    assert ok is True
    assert len(session.statements) == 1
    sql, params = _rendered(session.statements[0])
    assert sql.strip().upper().startswith("DELETE FROM")
    assert "project_file_comments" in sql
    assert _COMMENT_ID in params.values()


@pytest.mark.asyncio
async def test_delete_comment_raises_on_db_error():
    session = _CaptureSession(raise_on_execute=RuntimeError("boom"))
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        with pytest.raises(RuntimeError):
            await ProjectsRepository().delete_comment(str(_COMMENT_ID))


# ── service gate: _verify_file_in_project int/str compare ────────────────


@pytest.mark.asyncio
async def test_verify_file_in_project_int_project_id_matches_str_path_param():
    """Post-ORM, ``get_file_by_id`` returns ``project_id`` as a NATIVE int
    (``_row``/``_parity`` leave bigints native — the 5.3 trap), while the
    router path param arrives as a str. The gate must coerce both sides:
    ``{"project_id": 42}`` vs ``project_id="42"`` must NOT raise. Before the
    fix this raised ``ValueError("File not found in this project")`` on
    EVERY call, killing all 8 file-scoped endpoints end-to-end."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with patch.object(
        svc.repo,
        "get_file_by_id",
        new=AsyncMock(return_value={"id": 7, "project_id": 42}),
    ):
        out = await svc._verify_file_in_project("42", "7")
    assert out["project_id"] == 42


@pytest.mark.asyncio
async def test_verify_file_in_project_mismatch_still_raises():
    """A genuinely different project still raises (the guard must not become
    a pass-through)."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with patch.object(
        svc.repo,
        "get_file_by_id",
        new=AsyncMock(return_value={"id": 7, "project_id": 42}),
    ):
        with pytest.raises(ValueError):
            await svc._verify_file_in_project("43", "7")


class _GatePassed(Exception):
    """Sentinel raised by the first post-gate repo call — proves the ownership
    gate passed without executing any file IO further down the method."""


@pytest.mark.asyncio
async def test_upload_new_version_gate_accepts_int_project_id_vs_str_param():
    """``upload_new_version`` carried an inline copy of the same int/str
    compare. With the gate delegated to ``_verify_file_in_project``, a
    native-int row project_id must pass a str path param; the sentinel on
    ``get_next_version_number`` (the first post-gate call) stops the method
    before any file IO."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo, "get_project_by_id", new=AsyncMock(return_value={"id": 42})
        ),
        patch.object(
            svc.repo,
            "get_file_by_id",
            new=AsyncMock(return_value={"id": 7, "project_id": 42}),
        ),
        patch.object(
            svc.repo, "get_next_version_number", new=AsyncMock(side_effect=_GatePassed)
        ),
    ):
        with pytest.raises(_GatePassed):
            await svc.upload_new_version("42", "7", str(uuid.uuid4()), file=None)


@pytest.mark.asyncio
async def test_upload_new_version_gate_rejects_other_project():
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo, "get_project_by_id", new=AsyncMock(return_value={"id": 43})
        ),
        patch.object(
            svc.repo,
            "get_file_by_id",
            new=AsyncMock(return_value={"id": 7, "project_id": 42}),
        ),
        patch.object(
            svc.repo, "get_next_version_number", new=AsyncMock(side_effect=_GatePassed)
        ) as post_gate,
    ):
        with pytest.raises(ValueError):
            await svc.upload_new_version("43", "7", str(uuid.uuid4()), file=None)
    post_gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_review_status_gate_accepts_int_project_id_vs_str_param():
    """``update_review_status`` carried the third copy of the inline int/str
    compare; it must accept a native-int row project_id vs str path param."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo, "get_project_by_id", new=AsyncMock(return_value={"id": 42})
        ),
        patch.object(
            svc.repo,
            "get_file_by_id",
            new=AsyncMock(return_value={"id": 7, "project_id": 42}),
        ),
        patch.object(
            svc.repo,
            "update_review_status",
            new=AsyncMock(return_value={"id": 7, "review_status": "approved"}),
        ),
    ):
        out = await svc.update_review_status("42", "7", str(uuid.uuid4()), "approved")
    assert out["review_status"] == "approved"


@pytest.mark.asyncio
async def test_update_review_status_gate_rejects_other_project():
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo, "get_project_by_id", new=AsyncMock(return_value={"id": 43})
        ),
        patch.object(
            svc.repo,
            "get_file_by_id",
            new=AsyncMock(return_value={"id": 7, "project_id": 42}),
        ),
        patch.object(svc.repo, "update_review_status", new=AsyncMock()) as update_mock,
    ):
        with pytest.raises(ValueError):
            await svc.update_review_status("43", "7", str(uuid.uuid4()), "approved")
    update_mock.assert_not_awaited()


# ── service contract (keys flow through unchanged) ───────────────────────


@pytest.mark.asyncio
async def test_service_add_comment_keys_match_columns():
    """``add_comment`` assembles a dict whose keys are all real
    ``project_file_comments`` columns — no key the service builds gets
    silently dropped by ``_known_only`` as a phantom column."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo, "get_file_by_id", new=AsyncMock(return_value={"project_id": "1"})
        ),
        patch.object(
            svc.repo, "create_comment", new=AsyncMock(return_value={"id": "1"})
        ) as create_mock,
    ):
        await svc.add_comment(
            project_id="1",
            file_id="1",
            author_id=_AUTHOR_ID,
            content="hi",
            timestamp_seconds=1.0,
            version_id=str(_VERSION_ID),
            drawing_data={"x": 1},
        )
    sent = create_mock.call_args.args[0]
    column_keys = {p.key for p in ProjectFileComments.__mapper__.column_attrs}
    assert set(sent.keys()) <= column_keys


@pytest.mark.asyncio
async def test_service_delete_comment_author_check_uses_str_compare():
    """``delete_comment``'s author check compares the repo's (already
    str-parity) ``author_id`` against the caller's ``user_id`` string."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with (
        patch.object(
            svc.repo,
            "get_comment_by_id",
            new=AsyncMock(return_value={"id": "1", "author_id": _AUTHOR_ID}),
        ),
        patch.object(svc.repo, "delete_comment", new=AsyncMock(return_value=True)),
    ):
        assert await svc.delete_comment("1", _AUTHOR_ID) is True

    with patch.object(
        svc.repo,
        "get_comment_by_id",
        new=AsyncMock(return_value={"id": "1", "author_id": _AUTHOR_ID}),
    ):
        with pytest.raises(PermissionError):
            await svc.delete_comment("1", str(uuid.uuid4()))
