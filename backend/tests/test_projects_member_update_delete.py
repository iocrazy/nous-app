"""Regression + unit tests for the projects member update/remove fix.

Bug (found in the ORM cleanup reviews): ``ProjectsRepository.update_member`` /
``delete_member`` filtered a PHANTOM ``id`` column. ``project_members`` has a
composite PK ``(project_id, user_id)`` and NO ``id`` column, so PUT/DELETE
``/{project_id}/members/{member_id}`` was a SILENT NO-OP in production. The
route/wire ``member_id`` is the member's ``user_id`` (the frontend previously
sent ``member.id`` — undefined — which was the mirror of the same phantom).

These run in the UNIT suite (no DSN): the session + scopes are faked, and the
emitted SQLAlchemy statement is captured and compiled to prove the WHERE now
filters by ``user_id`` + ``project_id`` and never references a phantom ``id``.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.projects_repository as orm_mod
from app.models.teams import ProjectMembers
from app.repositories.projects_repository import ProjectsRepository

_USER_ID = str(uuid.uuid4())
_PROJECT_ID = 123456789


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _CaptureSession:
    """Records every statement passed to ``execute`` and returns a fixed row."""

    def __init__(self, returning_row=None):
        self.statements: list = []
        self._row = returning_row

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self._row)


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _member_row(role: str = "editor") -> ProjectMembers:
    return ProjectMembers(
        user_id=uuid.UUID(_USER_ID),
        project_id=_PROJECT_ID,
        role=role,
        joined_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        invited_by=None,
    )


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


# ── update_member ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_member_filters_user_id_project_id_not_phantom_id():
    """The UPDATE targets (project_id, user_id) and sets role — never a phantom
    ``id`` column. This is the regression gate for the silent-no-op bug."""
    session = _CaptureSession(returning_row=_member_row("editor"))
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        result = await ProjectsRepository().update_member(
            _USER_ID, str(_PROJECT_ID), {"role": "editor"}
        )

    assert len(session.statements) == 1
    sql, params = _rendered(session.statements[0])
    assert "project_members.user_id" in sql
    assert "project_members.project_id" in sql
    assert "project_members.id" not in sql  # no phantom id
    # The role change and both PK filters are bound.
    assert "editor" in params.values()
    assert _USER_ID in params.values()
    assert _PROJECT_ID in params.values()
    # Strategy-C parity dict returned (uuid → str, datetime → ISO str).
    assert result["user_id"] == _USER_ID
    assert result["project_id"] == _PROJECT_ID
    assert result["role"] == "editor"
    assert isinstance(result["joined_at"], str)


@pytest.mark.asyncio
async def test_update_member_no_match_returns_none():
    """A (project_id, user_id) that matches no row → RETURNING yields nothing →
    ``None`` (the service turns that into a 404)."""
    session = _CaptureSession(returning_row=None)
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        result = await ProjectsRepository().update_member(
            str(uuid.uuid4()), str(_PROJECT_ID), {"role": "viewer"}
        )
    assert result is None


# ── delete_member ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_member_filters_user_id_project_id_not_phantom_id():
    """The DELETE targets (project_id, user_id) and never a phantom ``id``."""
    session = _CaptureSession()
    with patch.object(orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        ok = await ProjectsRepository().delete_member(_USER_ID, str(_PROJECT_ID))

    assert ok is True
    assert len(session.statements) == 1
    sql, params = _rendered(session.statements[0])
    assert sql.strip().upper().startswith("DELETE FROM ")
    assert "project_members" in sql
    assert "project_members.user_id" in sql
    assert "project_members.project_id" in sql
    assert "project_members.id" not in sql  # no phantom id
    assert _USER_ID in params.values()
    assert _PROJECT_ID in params.values()


# ── service contract (no-match → 404 via ValueError) ─────────────────────


@pytest.mark.asyncio
async def test_service_update_member_role_raises_on_no_match():
    """``update_member_role`` raises ValueError (router → 404) when the repo
    reports no matching member."""
    from app.services.library.projects_service import ProjectsService

    svc = ProjectsService()
    with patch.object(svc.repo, "update_member", new=AsyncMock(return_value=None)):
        with pytest.raises(ValueError):
            await svc.update_member_role(str(_PROJECT_ID), _USER_ID, "viewer")
