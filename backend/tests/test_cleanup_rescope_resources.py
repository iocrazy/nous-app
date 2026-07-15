"""Schema-drift sweep: /cleanup re-scoped to resources.creator_id after
parsed_media.user_id was dropped (mig 083).

These are pure unit tests over the router-layer logic with mocked
service/repo/clients — no DB / network. They pin the post-model semantics:

  * cleanup delete (single + batch) operates on the CALLER's own resource via
    ResourcesService.permanent_delete (creator_id-scoped + shared-media GC),
    NOT a raw global parsed_media delete.
  * by-platform-id DELETE never touches global parsed_media; it only unlinks a
    resource the CALLER owns. A media row with no owning resource → 404.
  * /cleanup/storage scopes by resources.creator_id (owned media_ids), not the
    dropped parsed_media.user_id.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_USER = "u-cleanup-1"


def _patch_resources_service(fake_service):
    """Patch the lazily-imported ResourcesService inside _delete_user_media."""
    return patch(
        "app.services.library.resources_service.ResourcesService",
        return_value=fake_service,
    )


# ── cleanup delete → user's resource via permanent_delete ───────────────────


@pytest.mark.asyncio
async def test_delete_user_media_deletes_owned_resource():
    """_delete_user_media resolves the caller's resource and permanent_deletes
    it (creator-scoped + GC), returning True."""
    import importlib

    cleanup_router = importlib.import_module("app.api.cleanup_router")

    fake_service = MagicMock()
    fake_service.repo.get_resource_by_media_id_and_creator = AsyncMock(
        return_value={"id": "res-9", "creator_id": _USER, "media_id": "123"}
    )
    fake_service.permanent_delete = AsyncMock(return_value=True)

    with _patch_resources_service(fake_service):
        result = await cleanup_router._delete_user_media(123, _USER)

    assert result is True
    fake_service.repo.get_resource_by_media_id_and_creator.assert_awaited_once_with(
        "123", _USER
    )
    fake_service.permanent_delete.assert_awaited_once_with("res-9", _USER)


@pytest.mark.asyncio
async def test_delete_user_media_returns_false_when_not_owner():
    """No resource owned by the caller for this media → False (caller → 404).
    The global parsed_media row is never touched."""
    import importlib

    cleanup_router = importlib.import_module("app.api.cleanup_router")

    fake_service = MagicMock()
    fake_service.repo.get_resource_by_media_id_and_creator = AsyncMock(
        return_value=None
    )
    fake_service.permanent_delete = AsyncMock()

    with _patch_resources_service(fake_service):
        result = await cleanup_router._delete_user_media(123, _USER)

    assert result is False
    fake_service.permanent_delete.assert_not_awaited()


# ── cleanup action endpoint (delete branch) ─────────────────────────────────


@pytest.mark.asyncio
async def test_take_cleanup_action_delete_404_when_not_owned():
    import importlib

    cleanup_router = importlib.import_module("app.api.cleanup_router")
    from fastapi import HTTPException

    from app.schemas.cleanup import CleanupAction

    auth = SimpleNamespace(user_id=_USER)

    with patch.object(
        cleanup_router, "_delete_user_media", AsyncMock(return_value=False)
    ):
        with pytest.raises(HTTPException) as exc:
            await cleanup_router.take_cleanup_action(
                auth=auth, media_id=123, action=CleanupAction(action="delete")
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_take_cleanup_action_delete_ok_when_owned():
    import importlib

    cleanup_router = importlib.import_module("app.api.cleanup_router")
    from app.schemas.cleanup import CleanupAction

    auth = SimpleNamespace(user_id=_USER)

    with patch.object(
        cleanup_router, "_delete_user_media", AsyncMock(return_value=True)
    ):
        result = await cleanup_router.take_cleanup_action(
            auth=auth, media_id=123, action=CleanupAction(action="delete")
        )
    assert result == {"message": "Media deleted", "media_id": 123}


# ── /cleanup/storage re-scope ───────────────────────────────────────────────


class _FakeQuery:
    """Minimal chainable stand-in for the supabase query builder."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeClient:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(self._tables[name])


def _storage_read_scope(resources_media_ids, parsed_rows):
    """A ``read_scope()`` stand-in for get_storage_breakdown's two ORM reads.

    The resources SELECT returns scalar media_ids (``.scalars().all()``); the
    parsed_media SELECT returns row mappings (``.mappings().all()``). Dispatch
    is by the table name rendered into the compiled statement."""

    class _List:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _List(self._rows)

        def mappings(self):
            return _List(self._rows)

    class _Session:
        async def execute(self, stmt):
            sql = str(stmt).lower()
            if "resources" in sql:
                return _Result(resources_media_ids)
            return _Result(parsed_rows)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


@pytest.mark.asyncio
async def test_storage_breakdown_scopes_by_owned_media():
    import importlib

    cleanup_router = importlib.import_module("app.api.cleanup_router")
    import app.db.session as db_session_mod

    auth = SimpleNamespace(user_id=_USER)
    parsed_rows = [
        {
            "id": 1,
            "storage_size": 1000,
            "media_type": "video",
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": 2,
            "storage_size": 500,
            "media_type": "carousel",
            "created_at": "2026-01-02T00:00:00Z",
        },
    ]

    with patch.object(
        db_session_mod,
        "read_scope",
        _storage_read_scope([1, 2, None], parsed_rows),
    ):
        result = await cleanup_router.get_storage_breakdown(auth=auth)

    assert result["total_videos"] == 2
    assert result["total_bytes"] == 1500
    assert result["by_type"]["video"] == 1000
    assert result["by_type"]["image"] == 500


@pytest.mark.asyncio
async def test_storage_breakdown_empty_when_user_owns_nothing():
    import importlib

    cleanup_router = importlib.import_module("app.api.cleanup_router")
    import app.db.session as db_session_mod

    auth = SimpleNamespace(user_id=_USER)

    with patch.object(db_session_mod, "read_scope", _storage_read_scope([], [])):
        result = await cleanup_router.get_storage_breakdown(auth=auth)

    assert result == {
        "by_type": {"video": 0, "image": 0, "other": 0},
        "by_month": [],
        "largest_videos": [],
        "total_bytes": 0,
        "total_videos": 0,
    }


def _scalar_read_scope(value):
    """A ``read_scope()`` stand-in whose session.execute().scalar() returns
    ``value`` — the parsed_media platform_id → id lookup is a scalar select."""

    class _Result:
        def scalar(self):
            return value

    class _Session:
        async def execute(self, stmt, params=None):
            return _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


# ── by-platform-id DELETE: never touches global parsed_media ────────────────


@pytest.mark.asyncio
async def test_by_platform_id_404_when_caller_owns_nothing():
    """No item in scope + the media row exists but the caller owns no resource
    for it → 404. The global parsed_media row must NOT be deleted."""
    from fastapi import HTTPException

    import app.api.resources_crud_router as rc
    import app.db.session as db_session_mod

    auth = SimpleNamespace(user_id=_USER)

    svc = MagicMock()
    svc.repo.get_resource_by_platform_id = AsyncMock(return_value=None)
    svc.repo.get_resource_by_media_id_and_creator = AsyncMock(return_value=None)
    svc.repo.get_first_resource_item = AsyncMock()
    svc.repo.delete_resource_item = AsyncMock()

    # parsed_media lookup returns an id (media exists globally) but caller owns
    # no resource → must 404, never delete.
    with (
        patch.object(rc, "ResourcesService", return_value=svc),
        patch.object(db_session_mod, "read_scope", _scalar_read_scope(777)),
    ):
        with pytest.raises(HTTPException) as exc:
            await rc.unlink_resource_by_platform_id(
                platform_id="abc", auth=auth, _scope=None, scope_id=None
            )

    assert exc.value.status_code == 404
    svc.repo.delete_resource_item.assert_not_awaited()


@pytest.mark.asyncio
async def test_by_platform_id_unlinks_caller_owned_resource_in_fallback():
    """No item in the requested scope, but the caller owns a resource for the
    media → unlink their first item (trigger GC). No global delete."""
    import app.api.resources_crud_router as rc
    import app.db.session as db_session_mod

    auth = SimpleNamespace(user_id=_USER)

    svc = MagicMock()
    # Resource exists but not in the requested scope (no item) → fall through.
    svc.repo.get_resource_by_platform_id = AsyncMock(return_value={"id": "res-5"})
    svc.repo.get_resource_item = AsyncMock(return_value=None)
    svc.repo.get_resource_by_media_id_and_creator = AsyncMock(
        return_value={"id": "res-5", "creator_id": _USER}
    )
    svc.repo.get_first_resource_item = AsyncMock(return_value={"id": "item-5"})
    svc.repo.delete_resource_item = AsyncMock(return_value=True)

    with (
        patch.object(rc, "ResourcesService", return_value=svc),
        patch(
            "app.services.library.resources_service._resolve_personal_team_id",
            AsyncMock(return_value="team-1"),
        ),
        patch.object(db_session_mod, "read_scope", _scalar_read_scope(777)),
    ):
        result = await rc.unlink_resource_by_platform_id(
            platform_id="abc", auth=auth, _scope=None, scope_id=None
        )

    assert result["success"] is True
    svc.repo.delete_resource_item.assert_awaited_once_with("item-5")
