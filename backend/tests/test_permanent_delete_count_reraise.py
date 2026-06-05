"""A4 Item 1 — data-loss hardening: count_resources_by_media_id RE-RAISES on
error, and the GC callers SKIP shared-file deletion on a count failure (never
delete shared physical files / parsed_media on an uncertain refcount).

The DANGEROUS value is a fabricated ``0``: a transient DB error returning 0 lets
the GC think no other resource references the shared parsed_media → it deletes
the SHARED physical files + parsed_media record that OTHER users still need.
A4 changes BOTH repo impls (ORM + legacy) to re-raise; the callers catch and
skip the destructive cleanup. No scheduled sweeper reclaims the skipped files —
they leak until a later successful permanent_delete or manual cleanup (accepted:
leaking on a rare transient error beats deleting files another user needs).

These are pure unit tests over the caller logic + the legacy repo's re-raise —
the repo is a stand-in whose ``count_resources_by_media_id`` raises, or (for the
legacy repo test) the supabase client is patched to raise. No DB / network.
INERT wrt the scope flag (this is about error propagation, not enforcement)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.resources_repository import ResourcesRepository
from app.services.library.resources_service import ResourcesService

_USER = "u-a4-items1"


class _CountRaises(Exception):
    """Stand-in for a transient DB error inside the refcount query."""


def _service_with_repo(repo) -> ResourcesService:
    svc = ResourcesService.__new__(ResourcesService)  # skip __init__'s real repo
    svc.repo = repo
    return svc


# ── permanent_delete ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permanent_delete_skips_file_gc_when_count_raises():
    """count failure → DB row delete already done → SKIP shared-file/media GC,
    do NOT raise out of permanent_delete (return contract preserved)."""
    media_id = "media-shared-1"
    resource = {"id": "res-1", "creator_id": _USER, "media_id": media_id}

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    repo.delete_resource = AsyncMock(return_value=True)
    repo.count_resources_by_media_id = AsyncMock(side_effect=_CountRaises("db down"))

    svc = _service_with_repo(repo)
    # Spy on the destructive helpers — they must NOT be called on count failure.
    svc._delete_physical_files = MagicMock()
    svc._delete_media_record = AsyncMock()

    result = await svc.permanent_delete("res-1", _USER)

    # Return contract preserved (the DB row delete already succeeded).
    assert result is True
    # The DB row was deleted exactly once.
    repo.delete_resource.assert_awaited_once_with("res-1")
    # Shared-file + media GC SKIPPED — the dangerous deletion never happened.
    svc._delete_physical_files.assert_not_called()
    svc._delete_media_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_permanent_delete_gcs_files_when_count_zero():
    """Sanity (the happy path still works): count==0 → shared files + media
    record ARE deleted."""
    media_id = "media-shared-2"
    resource = {"id": "res-2", "creator_id": _USER, "media_id": media_id}

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    repo.delete_resource = AsyncMock(return_value=True)
    repo.count_resources_by_media_id = AsyncMock(return_value=0)

    svc = _service_with_repo(repo)
    svc._delete_physical_files = MagicMock()
    svc._delete_media_record = AsyncMock()

    result = await svc.permanent_delete("res-2", _USER)

    assert result is True
    svc._delete_physical_files.assert_called_once_with(resource)
    svc._delete_media_record.assert_awaited_once_with(media_id)


@pytest.mark.asyncio
async def test_permanent_delete_skips_gc_when_count_nonzero():
    """Sanity: count>0 (another user still references) → no GC, no raise."""
    media_id = "media-shared-3"
    resource = {"id": "res-3", "creator_id": _USER, "media_id": media_id}

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    repo.delete_resource = AsyncMock(return_value=True)
    repo.count_resources_by_media_id = AsyncMock(return_value=1)

    svc = _service_with_repo(repo)
    svc._delete_physical_files = MagicMock()
    svc._delete_media_record = AsyncMock()

    result = await svc.permanent_delete("res-3", _USER)

    assert result is True
    svc._delete_physical_files.assert_not_called()
    svc._delete_media_record.assert_not_awaited()


# ── cleanup_expired_trash ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_expired_trash_count_raise_skips_only_that_resource():
    """A count raise on ONE resource must NOT abort the whole sweep: that
    resource is skipped (no shared-file GC, not counted as cleaned), the next
    resource is still processed. Never GC on an uncertain refcount."""
    shared = "media-shared"
    res_bad = {"id": "res-bad", "media_id": shared}
    res_ok = {"id": "res-ok", "media_id": "media-solo"}

    repo = MagicMock()
    repo.get_expired_trashed_resources = AsyncMock(return_value=[res_bad, res_ok])
    repo.delete_resource = AsyncMock(return_value=True)

    # First resource's count raises; the second returns 0 (GC its solo media).
    repo.count_resources_by_media_id = AsyncMock(
        side_effect=[_CountRaises("db blip"), 0]
    )

    svc = _service_with_repo(repo)
    svc._delete_physical_files = MagicMock()
    svc._delete_media_record = AsyncMock()

    cleaned = await svc.cleanup_expired_trash(older_than_days=30)

    # Only the second resource was cleaned (the first was skipped on count fail).
    assert cleaned == 1
    # Both rows had a DELETE attempted (delete happens before the count).
    assert repo.delete_resource.await_count == 2
    # The bad resource's SHARED files were NEVER deleted (count raised first).
    svc._delete_physical_files.assert_called_once_with(res_ok)
    svc._delete_media_record.assert_awaited_once_with("media-solo")


# ── legacy (supabase-py) repo — the LIVE prod default path ──────────────────


@pytest.mark.asyncio
async def test_legacy_repo_count_reraises_on_error_never_returns_zero():
    """``ResourcesRepository.count_resources_by_media_id`` (legacy supabase-py,
    the LIVE prod default while USE_ORM_RESOURCES is False) must RE-RAISE on a
    DB error, NOT return a fabricated 0. We patch ``_get_client`` to raise and
    assert the exception propagates (mirrors the ORM repo's re-raise so the
    fabricated-0 data-loss path is closed on BOTH impls)."""

    class _Boom(Exception):
        pass

    repo = ResourcesRepository()
    with patch.object(
        ResourcesRepository, "_get_client", AsyncMock(side_effect=_Boom("db down"))
    ):
        with pytest.raises(_Boom):
            await repo.count_resources_by_media_id("123")
