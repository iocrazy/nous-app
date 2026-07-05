"""Regression test for the script/storyboard project-create bigint DataError.

Bug (prod 500 on "New Script" / "New Storyboard"): ``require_team_id()``
(app/core/deps.py) returns team_id as ``str``. The service layer
(``ScriptService.create_project`` / ``StoryboardService.create_project``)
passed that str straight into the repo ``create(data)`` call, which bound it
into an ``INSERT ... team_id`` (and ``project_id``) column typed BIGINT.
asyncpg's int8 codec is strict about str binds and raised:

    invalid input for query argument $2: '310812366953241'
    ('str' object cannot be interpreted as an integer)

This is a DB-free, capture-the-emitted-statement test (pattern copied from
``tests/test_projects_member_update_delete.py``): it fakes ``write_scope()``,
captures the SQLAlchemy INSERT statement that would have been executed, and
asserts the compiled bind params for the bigint FK columns are ``int``, not
``str``. It does NOT touch a real database.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.script_repository as script_orm_mod
import app.repositories.storyboard_repository as storyboard_orm_mod
from app.models.scripts import ScriptProjects
from app.models.storyboard import StoryboardProjects
from app.repositories.script_repository import ScriptProjectRepository
from app.repositories.storyboard_repository import StoryboardProjectRepository

_USER_ID = str(uuid.uuid4())
_TEAM_ID_STR = "310812366953241"
_PROJECT_ID_STR = "42"


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


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


def _script_project_row() -> ScriptProjects:
    return ScriptProjects(
        id=999,
        project_id=42,
        team_id=int(_TEAM_ID_STR),
        name="T",
        created_by=uuid.UUID(_USER_ID),
        display_code=None,
        description=None,
        settings_json={},
        viewport_json=None,
        status="active",
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        genre=None,
    )


def _storyboard_project_row() -> StoryboardProjects:
    return StoryboardProjects(
        id=999,
        team_id=int(_TEAM_ID_STR),
        created_by=uuid.UUID(_USER_ID),
        name="T",
        status="active",
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        description=None,
        cover_image_url=None,
        viewport_json=None,
        settings_json=None,
        project_id=None,
        display_code=None,
    )


@pytest.mark.asyncio
async def test_script_project_create_binds_int_team_id_and_project_id():
    """ScriptProjectRepository.create must coerce str team_id/project_id to
    int before the INSERT bind (asyncpg int8 codec is strict)."""
    session = _CaptureSession(returning_row=_script_project_row())
    with patch.object(script_orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptProjectRepository().create(
            {
                "team_id": _TEAM_ID_STR,
                "project_id": _PROJECT_ID_STR,
                "name": "T",
                "created_by": _USER_ID,
                "settings_json": {},
            }
        )

    assert len(session.statements) == 1
    _, params = _rendered(session.statements[0])

    team_id_val = params.get("team_id")
    project_id_val = params.get("project_id")
    assert team_id_val is not None, f"team_id not bound in params: {params}"
    assert project_id_val is not None, f"project_id not bound in params: {params}"
    assert isinstance(
        team_id_val, int
    ), f"team_id bound as {type(team_id_val)} ({team_id_val!r}), expected int"
    assert isinstance(
        project_id_val, int
    ), f"project_id bound as {type(project_id_val)} ({project_id_val!r}), expected int"


@pytest.mark.asyncio
async def test_storyboard_project_create_binds_int_team_id():
    """StoryboardProjectRepository.create must coerce str team_id to int
    before the INSERT bind (asyncpg int8 codec is strict)."""
    session = _CaptureSession(returning_row=_storyboard_project_row())
    with patch.object(storyboard_orm_mod, "write_scope", lambda: _ScopeCtx(session)):
        await StoryboardProjectRepository().create(
            {
                "team_id": _TEAM_ID_STR,
                "created_by": _USER_ID,
                "name": "T",
            }
        )

    assert len(session.statements) == 1
    _, params = _rendered(session.statements[0])

    team_id_val = params.get("team_id")
    assert team_id_val is not None, f"team_id not bound in params: {params}"
    assert isinstance(
        team_id_val, int
    ), f"team_id bound as {type(team_id_val)} ({team_id_val!r}), expected int"
