"""Overwrite-current-version content endpoint (text resource editing).

Pure unit tests: the service's repo + storage helpers are mocked, so no DB.
Mirrors test_version_service.py conventions (patch collaborators, call the
service method directly)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _svc():
    from app.services.library.resources_service import ResourcesService

    svc = ResourcesService()
    return svc


async def test_overwrite_updates_current_version_row(tmp_path):
    svc = _svc()
    svc.repo = MagicMock()
    svc.repo.get_resource_by_id = AsyncMock(
        return_value={"id": "10", "filename": "notes.md", "current_version": 1}
    )
    svc.repo.get_version_by_id = AsyncMock(
        return_value={"id": "77", "resource_id": "10", "version_number": 1}
    )
    svc.repo.get_first_resource_item = AsyncMock(return_value={"scope_id": "42"})
    svc.repo.update_version = AsyncMock(
        side_effect=lambda vid, data: {"id": vid, **data}
    )
    svc.repo.update_resource = AsyncMock(
        side_effect=lambda rid, data: {"id": rid, **data}
    )

    stored = SimpleNamespace(
        file_path="sb://library/t42/ab/cd/deadbeef.md", size_bytes=12
    )
    file = SimpleNamespace(filename="notes.md", content_type="text/markdown", size=12)

    with (
        patch(
            "app.services.library.resources_service.unified_storage_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.library.resources_service.stream_upload_to_disk",
            AsyncMock(return_value=(12, "deadbeef")),
        ),
        patch(
            "app.services.library.resources_service.sniff_mime",
            return_value="text/markdown",
        ),
        patch(
            "app.services.library.resources_service.store_local_file",
            AsyncMock(return_value=stored),
        ),
    ):
        result = await svc.overwrite_version_content(
            resource_id="10", version_id="77", user_id="u1", file=file
        )

    svc.repo.update_version.assert_awaited_once()
    called_vid, called_data = svc.repo.update_version.await_args.args
    assert called_vid == "77"
    assert called_data["file_path"] == "sb://library/t42/ab/cd/deadbeef.md"
    assert called_data["file_size_bytes"] == 12
    assert called_data["file_hash"] == "deadbeef"
    assert result["file_path"] == "sb://library/t42/ab/cd/deadbeef.md"

    # Fix 1: overwriting the CURRENT version must also repoint the parent
    # resources row — the detail page / downloads read resources.file_path,
    # not the version row.
    svc.repo.update_resource.assert_awaited_once()
    called_rid, called_res_data = svc.repo.update_resource.await_args.args
    assert called_rid == "10"
    assert called_res_data["file_path"] == "sb://library/t42/ab/cd/deadbeef.md"
    assert called_res_data["file_hash"] == "deadbeef"


async def test_overwrite_non_current_version_does_not_touch_resource_row(tmp_path):
    """Overwriting a version that is NOT the resource's current_version must
    only touch the version row — the parent resources row still points at
    whatever version is actually current."""
    svc = _svc()
    svc.repo = MagicMock()
    svc.repo.get_resource_by_id = AsyncMock(
        return_value={"id": "10", "filename": "notes.md", "current_version": 2}
    )
    svc.repo.get_version_by_id = AsyncMock(
        return_value={"id": "77", "resource_id": "10", "version_number": 1}
    )
    svc.repo.get_first_resource_item = AsyncMock(return_value={"scope_id": "42"})
    svc.repo.update_version = AsyncMock(
        side_effect=lambda vid, data: {"id": vid, **data}
    )
    svc.repo.update_resource = AsyncMock(
        side_effect=lambda rid, data: {"id": rid, **data}
    )

    stored = SimpleNamespace(
        file_path="sb://library/t42/ab/cd/deadbeef.md", size_bytes=12
    )
    file = SimpleNamespace(filename="notes.md", content_type="text/markdown", size=12)

    with (
        patch(
            "app.services.library.resources_service.unified_storage_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.library.resources_service.stream_upload_to_disk",
            AsyncMock(return_value=(12, "deadbeef")),
        ),
        patch(
            "app.services.library.resources_service.sniff_mime",
            return_value="text/markdown",
        ),
        patch(
            "app.services.library.resources_service.store_local_file",
            AsyncMock(return_value=stored),
        ),
    ):
        await svc.overwrite_version_content(
            resource_id="10", version_id="77", user_id="u1", file=file
        )

    svc.repo.update_version.assert_awaited_once()
    svc.repo.update_resource.assert_not_awaited()


async def test_overwrite_rejects_version_from_other_resource():
    svc = _svc()
    svc.repo = MagicMock()
    svc.repo.get_resource_by_id = AsyncMock(return_value={"id": "10"})
    svc.repo.get_version_by_id = AsyncMock(
        return_value={"id": "77", "resource_id": "999"}
    )

    file = SimpleNamespace(filename="x", content_type="text/plain", size=1)
    with pytest.raises(ValueError):
        await svc.overwrite_version_content(
            resource_id="10", version_id="77", user_id="u1", file=file
        )


async def test_overwrite_invalidates_media_path_cache(tmp_path):
    """After overwrite, the /media/{id} path cache entry must be dropped so the
    detail page shows the new bytes immediately instead of after the TTL."""
    from app.services.media import media_path_cache

    media_path_cache.clear()
    media_path_cache.put("10", "file", "sb://old", "u1", ())

    svc = _svc()
    svc.repo = MagicMock()
    svc.repo.get_resource_by_id = AsyncMock(
        return_value={"id": "10", "filename": "n.md", "current_version": 1}
    )
    svc.repo.get_version_by_id = AsyncMock(
        return_value={"id": "77", "resource_id": "10", "version_number": 1}
    )
    svc.repo.get_first_resource_item = AsyncMock(return_value={"scope_id": "42"})
    svc.repo.update_version = AsyncMock(
        side_effect=lambda vid, data: {"id": vid, **data}
    )
    svc.repo.update_resource = AsyncMock(
        side_effect=lambda rid, data: {"id": rid, **data}
    )

    stored = SimpleNamespace(file_path="sb://library/t42/ab/cd/new.md", size_bytes=5)
    file = SimpleNamespace(filename="n.md", content_type="text/markdown", size=5)
    with (
        patch(
            "app.services.library.resources_service.unified_storage_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.library.resources_service.stream_upload_to_disk",
            AsyncMock(return_value=(5, "newhash")),
        ),
        patch(
            "app.services.library.resources_service.sniff_mime",
            return_value="text/markdown",
        ),
        patch(
            "app.services.library.resources_service.store_local_file",
            AsyncMock(return_value=stored),
        ),
    ):
        await svc.overwrite_version_content(
            resource_id="10", version_id="77", user_id="u1", file=file
        )

    assert media_path_cache.get("10", "file") is None
    media_path_cache.clear()


async def test_overwrite_store_failure_raises_and_leaves_version_untouched(tmp_path):
    """2026-09-07 hard-fail: S3 refusing the bytes must not repoint the
    version at a filesystem copy — the version row stays as it was."""
    from app.services.library.storage_errors import ObjectStoreWriteFailed

    svc = _svc()
    svc.repo = MagicMock()
    svc.repo.get_resource_by_id = AsyncMock(
        return_value={"id": "10", "filename": "notes.md", "current_version": 1}
    )
    svc.repo.get_version_by_id = AsyncMock(
        return_value={"id": "77", "resource_id": "10", "version_number": 1}
    )
    svc.repo.get_first_resource_item = AsyncMock(return_value={"scope_id": "42"})
    svc.repo.update_version = AsyncMock()
    svc.repo.update_resource = AsyncMock()
    file = SimpleNamespace(filename="notes.md", content_type="text/markdown", size=12)

    with (
        patch(
            "app.services.library.resources_service.unified_storage_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.library.resources_service.stream_upload_to_disk",
            AsyncMock(return_value=(12, "deadbeef")),
        ),
        patch(
            "app.services.library.resources_service.sniff_mime",
            return_value="text/markdown",
        ),
        patch(
            "app.services.library.resources_service.store_local_file",
            AsyncMock(side_effect=RuntimeError("storage-api unreachable")),
        ),
    ):
        with pytest.raises(ObjectStoreWriteFailed) as excinfo:
            await svc.overwrite_version_content(
                resource_id="10", version_id="77", user_id="u1", file=file
            )

    assert excinfo.value.details["where"] == "overwrite_version_content"
    assert excinfo.value.details["version_id"] == "77"
    svc.repo.update_version.assert_not_awaited()
    svc.repo.update_resource.assert_not_awaited()
