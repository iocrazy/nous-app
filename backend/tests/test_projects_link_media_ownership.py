"""Unit tests for the link_media ownership check (PR-A1 security batch).

parsed_media has NO ownership column (``user_id`` was dropped in migration
083); ownership lives on ``resources.creator_id`` via ``resources.media_id``.
``ProjectsService.link_media`` therefore resolves the media's owner through
``ProjectsRepository.get_media_creator`` (a join-through-resources read) and
raises PermissionError when the caller is not the creator. A ``None`` creator
(orphan/system media without a resource row) is allowed through.

Run with:
    cd backend && uv run pytest tests/test_projects_link_media_ownership.py -q
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.projects_repository as orm_mod
from app.repositories.projects_repository import ProjectsRepository
from app.services.library.projects_service import ProjectsService

_OWNER_ID = str(uuid.uuid4())
_OTHER_ID = str(uuid.uuid4())
_PROJECT_ID = "123456789"
_MEDIA_ID = "987654321"

_PROJECT_ROW = {"id": int(_PROJECT_ID), "owner_id": _OWNER_ID, "team_id": None}
_MEDIA_ROW = {"id": int(_MEDIA_ID), "title": "Test Clip", "duration": None}


def _svc_with_mocks(
    media_creator: str | None,
) -> tuple[ProjectsService, AsyncMock]:
    """A ProjectsService whose repo reads are mocked; returns (svc, create_file)."""
    svc = ProjectsService()
    svc.repo.get_project_by_id = AsyncMock(return_value=_PROJECT_ROW)
    svc.repo.get_media_metadata = AsyncMock(return_value=_MEDIA_ROW)
    svc.repo.get_media_creator = AsyncMock(return_value=media_creator)
    create_file = AsyncMock(return_value={"id": 1, "media_id": _MEDIA_ID})
    svc.repo.create_file = create_file
    return svc, create_file


# ── service: ownership semantics ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_media_creator_mismatch_raises_permission_error() -> None:
    """Media owned by someone else → PermissionError (router → 403), and no
    project_files row is created."""
    svc, create_file = _svc_with_mocks(media_creator=_OWNER_ID)
    with pytest.raises(PermissionError):
        await svc.link_media(
            project_id=_PROJECT_ID, media_id=_MEDIA_ID, user_id=_OTHER_ID
        )
    create_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_media_creator_none_is_allowed() -> None:
    """No resource row (orphan/system media) → creator is None → allowed."""
    svc, create_file = _svc_with_mocks(media_creator=None)
    result = await svc.link_media(
        project_id=_PROJECT_ID, media_id=_MEDIA_ID, user_id=_OTHER_ID
    )
    assert result["media_id"] == _MEDIA_ID
    create_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_link_media_creator_match_is_allowed() -> None:
    """Caller IS the creator → allowed."""
    svc, create_file = _svc_with_mocks(media_creator=_OWNER_ID)
    result = await svc.link_media(
        project_id=_PROJECT_ID, media_id=_MEDIA_ID, user_id=_OWNER_ID
    )
    assert result["media_id"] == _MEDIA_ID
    create_file.assert_awaited_once()


# ── repository: emitted statement targets resources.creator_id ──────────


class _ScalarSession:
    """Fake session capturing the statement passed to ``scalar``."""

    def __init__(self, value):
        self.statements: list = []
        self._value = value

    async def scalar(self, stmt):
        self.statements.append(stmt)
        return self._value


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_get_media_creator_selects_resources_creator_by_media_id() -> None:
    """The SELECT reads resources.creator_id filtered by resources.media_id
    (parsed_media itself has no ownership column — mig 083)."""
    creator_uuid = uuid.UUID(_OWNER_ID)
    session = _ScalarSession(creator_uuid)
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        result = await ProjectsRepository().get_media_creator(_MEDIA_ID)

    assert result == _OWNER_ID  # uuid → str
    assert len(session.statements) == 1
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "resources.creator_id" in sql
    assert "resources.media_id" in sql
    assert int(_MEDIA_ID) in dict(compiled.params).values()


@pytest.mark.asyncio
async def test_get_media_creator_returns_none_when_no_resource_row() -> None:
    session = _ScalarSession(None)
    with patch.object(orm_mod, "read_scope", lambda: _ScopeCtx(session)):
        assert await ProjectsRepository().get_media_creator(_MEDIA_ID) is None
