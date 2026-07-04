"""Unit tests for ``ResourcesService.permanent_delete_folder`` (ORM-ported).

Regression guard: ``permanent_delete_folder`` used to call the removed
``repo._get_client()`` REST bypass in two loops. These tests use a REAL
``ResourcesRepository`` on the service (so a missing method surfaces as a real
AttributeError, not a MagicMock auto-attr) with only the leaf DATA-ACCESS
methods stubbed — exercising the actual service→repo call graph. If the service
reached for ``_get_client`` again, ``test_*_does_not_touch_get_client`` would
fail with AttributeError.

Covers: the descendant-folder collection (include_trashed=True), the per-folder
resource purge via ``list_resources_in_folder(include_trashed=True)``, the
children-first folder deletes, the counts, and the already-deleted / not-owned
skip path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.resources_repository import ResourcesRepository
from app.services.library.resources_service import ResourcesService


def _service_with_stubbed_repo() -> ResourcesService:
    """Real service + real repo (no _get_client), leaf DB methods stubbed."""
    svc = ResourcesService()
    assert isinstance(svc.repo, ResourcesRepository)
    assert not hasattr(svc.repo, "_get_client")  # the bypass is gone
    # Physical-side effects are out of scope for these DB-flow tests.
    svc._delete_physical_files = MagicMock()
    svc._delete_media_record = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_permanent_delete_folder_walks_tree_and_purges(monkeypatch) -> None:
    svc = _service_with_stubbed_repo()

    # folder "1" has descendants "2" and "3".
    svc.repo.get_descendant_folder_ids = AsyncMock(return_value=["2", "3"])

    # resources per folder: folder 1 → r10, folder 2 → r20, folder 3 → none.
    async def _list(fid, *, include_trashed=False):
        assert include_trashed is True  # permanent purge reaches trashed rows
        return {
            "1": [{"id": 10, "created_at": "t"}],
            "2": [{"id": 20, "created_at": "t"}],
            "3": [],
        }[str(fid)]

    svc.repo.list_resources_in_folder = AsyncMock(side_effect=_list)
    svc.repo.delete_folder = AsyncMock(return_value=True)
    # Let the REAL permanent_delete run against stubbed leaf repo reads/writes.
    svc.repo.get_resource_by_id = AsyncMock(
        return_value={"creator_id": "owner-1", "media_id": None}
    )
    svc.repo.delete_resource = AsyncMock(return_value=True)

    result = await svc.permanent_delete_folder("1", "owner-1")

    assert result == {"deleted_folders": 3, "deleted_resources": 2}
    # Descendants fetched with include_trashed=True.
    svc.repo.get_descendant_folder_ids.assert_awaited_once_with(
        "1", include_trashed=True
    )
    # Each of the 3 folders was scanned for resources.
    assert svc.repo.list_resources_in_folder.await_count == 3
    # Both owned resources permanently deleted.
    assert svc.repo.delete_resource.await_count == 2
    # Folders deleted children-first: reversed(["1","2","3"]) == 3,2,1.
    assert [c.args[0] for c in svc.repo.delete_folder.await_args_list] == [
        "3",
        "2",
        "1",
    ]


@pytest.mark.asyncio
async def test_permanent_delete_folder_skips_not_owned_and_missing(monkeypatch) -> None:
    svc = _service_with_stubbed_repo()
    svc.repo.get_descendant_folder_ids = AsyncMock(return_value=[])
    svc.repo.list_resources_in_folder = AsyncMock(
        return_value=[{"id": 10}, {"id": 11}, {"id": 12}]
    )
    svc.repo.delete_folder = AsyncMock(return_value=True)

    # r10 owned, r11 not owned (PermissionError), r12 already gone (ValueError).
    async def _get(rid):
        return {
            "10": {"creator_id": "owner-1", "media_id": None},
            "11": {"creator_id": "someone-else", "media_id": None},
            "12": None,
        }[str(rid)]

    svc.repo.get_resource_by_id = AsyncMock(side_effect=_get)
    svc.repo.delete_resource = AsyncMock(return_value=True)

    result = await svc.permanent_delete_folder("1", "owner-1")

    # Only the owned, still-present resource counts; the other two are skipped.
    assert result == {"deleted_folders": 1, "deleted_resources": 1}
    assert svc.repo.delete_resource.await_count == 1


@pytest.mark.asyncio
async def test_permanent_delete_folder_does_not_touch_get_client(monkeypatch) -> None:
    """The port removed the REST bypass: exercising the full flow must NEVER
    reach for repo._get_client (its absence on the real repo would AttributeError
    — the exact 500 this regression caused in prod)."""
    svc = _service_with_stubbed_repo()
    svc.repo.get_descendant_folder_ids = AsyncMock(return_value=["2"])
    svc.repo.list_resources_in_folder = AsyncMock(return_value=[])
    svc.repo.delete_folder = AsyncMock(return_value=True)

    # No exception (AttributeError for _get_client) is raised.
    result = await svc.permanent_delete_folder("1", "owner-1")
    assert result == {"deleted_folders": 2, "deleted_resources": 0}
