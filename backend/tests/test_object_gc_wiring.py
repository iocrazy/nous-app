"""Wiring regression tests: ResourcesService._delete_physical_files /
delete_version route sb:// rows through object_gc.delete_object_if_unreferenced,
while legacy filesystem rows keep their original behavior untouched.

Spec: docs/superpowers/specs/2026-08-03-reference-safe-object-deletion-design.md
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.library.resources_service import ResourcesService

pytestmark = pytest.mark.asyncio

RID = "9000000000000000001"
MEDIA_ID = "8000000000000000002"
SB_FILE = "sb://library/t5/aa/bb/deadbeef.mp4"
SB_COVER = "sb://library/t5/cc/dd/cover.jpg"
SB_THUMB = "sb://library/t5/ee/ff/thumb.jpg"


def _service() -> ResourcesService:
    svc = ResourcesService.__new__(ResourcesService)  # skip __init__'s real repo
    svc.repo = MagicMock()
    return svc


def _calls_for(mock: AsyncMock, needle: str):
    return [c for c in mock.await_args_list if needle in c.args[0]]


# ── _delete_physical_files ──────────────────────────────────────────────────


async def test_delete_physical_files_sb_rows_go_through_object_gc():
    """All three sb:// fields (file_path/cover_image_path/thumbnail_path) are
    routed through delete_object_if_unreferenced, excluding this resource's
    own id AND its originating parsed_media id (the parsed_media row is still
    live at this point — deleted only after this call, by
    _delete_media_record — so it must be excluded or a shared key would see
    its own soon-to-be-orphaned parsed_media row as a live reference and
    never get cleaned up)."""
    svc = _service()
    resource = {
        "id": RID,
        "media_id": MEDIA_ID,
        "file_path": SB_FILE,
        "cover_image_path": SB_COVER,
        "thumbnail_path": SB_THUMB,
    }
    gc = AsyncMock(return_value="deleted")

    with patch(
        "app.services.library.resources_service.delete_object_if_unreferenced", gc
    ):
        await svc._delete_physical_files(resource)

    expected_exclude = {"resources": [RID], "parsed_media": [MEDIA_ID]}
    assert _calls_for(gc, SB_FILE)[0].kwargs["exclude"] == expected_exclude
    assert _calls_for(gc, SB_COVER)[0].kwargs["exclude"] == expected_exclude
    assert _calls_for(gc, SB_THUMB)[0].kwargs["exclude"] == expected_exclude


async def test_delete_physical_files_always_clears_derived_prefix():
    """derived/{rid}/ (thumbnails + preview sprite) is namespaced by
    resource_id alone — never shared — so it is always removed outright, no
    reference check / no exclude needed."""
    svc = _service()
    resource = {"id": RID, "media_id": None}
    gc = AsyncMock(return_value="deleted")

    with patch(
        "app.services.library.resources_service.delete_object_if_unreferenced", gc
    ):
        await svc._delete_physical_files(resource)

    derived_calls = _calls_for(gc, f"derived/{RID}/")
    assert len(derived_calls) == 1
    assert derived_calls[0].args[0] == f"sb://library/derived/{RID}/"
    assert (
        "exclude" not in derived_calls[0].kwargs
        or derived_calls[0].kwargs.get("exclude") is None
    )


async def test_delete_physical_files_legacy_fs_untouched(tmp_path):
    """A legacy filesystem row must NOT be routed through object_gc at all —
    it keeps the original shutil-based directory removal, unchanged."""
    svc = _service()
    rel = f"global/resources/web/douyin/{RID}/clip.mp4"
    real_file = tmp_path / rel
    real_file.parent.mkdir(parents=True, exist_ok=True)
    real_file.write_bytes(b"fake-mp4")

    resource = {"id": RID, "media_id": None, "file_path": rel}
    gc = AsyncMock(return_value="deleted")

    with (
        patch("app.services.library.resources_service.settings") as mock_settings,
        patch(
            "app.services.library.resources_service.delete_object_if_unreferenced",
            gc,
        ),
    ):
        mock_settings.DOWNLOAD_PATH = str(tmp_path)
        await svc._delete_physical_files(resource)

    # The legacy directory was actually removed from disk.
    assert not real_file.exists()
    # object_gc was called only for the derived/{rid}/ prefix — never for the
    # legacy file_path.
    assert not _calls_for(gc, rel)
    assert len(_calls_for(gc, f"derived/{RID}/")) == 1


# ── delete_version ───────────────────────────────────────────────────────────


def _versions_repo(resource: dict, versions: list[dict]):
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    repo.get_versions = AsyncMock(return_value=versions)
    repo.delete_version = AsyncMock(return_value=True)
    return repo


async def test_delete_version_sb_file_path_excludes_own_version_id():
    """A version's sb:// file_path is routed through object_gc, excluding
    THIS version's id (the resource_versions row hasn't been deleted yet at
    this point in the call — repo.delete_version runs afterward — so without
    the exclude the row would see itself as a live reference and never
    delete)."""
    resource_id = RID
    version_id = "7000000000000000003"
    resource = {"id": resource_id, "current_version": 2}
    versions = [
        {"id": version_id, "version_number": 1, "file_path": SB_FILE},
        {"id": "other", "version_number": 2, "file_path": "sb://library/x/y/z.mp4"},
    ]
    svc = ResourcesService.__new__(ResourcesService)
    svc.repo = _versions_repo(resource, versions)
    gc = AsyncMock(return_value="deleted")

    with patch(
        "app.services.library.resources_service.delete_object_if_unreferenced", gc
    ):
        result = await svc.delete_version(resource_id, version_id, "user-1")

    assert result is True
    file_calls = _calls_for(gc, SB_FILE)
    assert len(file_calls) == 1
    assert file_calls[0].kwargs["exclude"] == {"resource_versions": [version_id]}
    svc.repo.delete_version.assert_awaited_once_with(version_id)


async def test_delete_version_hls_prefix_cleared_when_present():
    """A version with hls_path set gets its whole HLS tree removed as a
    prefix (not just the master.m3u8 file hls_path points at), with no
    reference check (HLS output is namespaced by resource_id+version_id,
    never shared)."""
    resource_id = RID
    version_id = "7000000000000000003"
    resource = {"id": resource_id, "current_version": 2}
    versions = [
        {
            "id": version_id,
            "version_number": 1,
            "file_path": None,
            "hls_path": "sb://library/hls/9000000000000000001/7000000000000000003/master.m3u8",
        },
        {"id": "other", "version_number": 2, "file_path": None},
    ]
    svc = ResourcesService.__new__(ResourcesService)
    svc.repo = _versions_repo(resource, versions)
    gc = AsyncMock(return_value="deleted")

    with patch(
        "app.services.library.resources_service.delete_object_if_unreferenced", gc
    ):
        await svc.delete_version(resource_id, version_id, "user-1")

    expected_prefix = f"sb://library/hls/{resource_id}/{version_id}/"
    hls_calls = _calls_for(gc, expected_prefix)
    assert len(hls_calls) == 1
    # No exclude — a prefix delete never queries references.
    assert hls_calls[0].kwargs == {}


async def test_delete_version_current_switch_happens_before_object_cleanup():
    """I4: deleting the CURRENT version used to clean up its physical file
    BEFORE switching resources.file_path to the new current version. Under
    content dedup, resources.file_path is routinely the exact same raw sb://
    string as the version being deleted (this is the entire reason
    object_gc's reference query exists) — cleaning up first meant that
    column always looked like a live self-reference, so a current-version
    delete permanently leaked its object every single time (unreachable
    today — 0 multi-version resources in production — but a guaranteed leak
    the moment any multi-version resource exists). The fix reorders: switch
    set_current_version FIRST, clean up the deleted version's object AFTER —
    this test asserts that exact call order."""
    resource_id = RID
    version_id = "7000000000000000003"
    resource = {"id": resource_id, "current_version": 1}
    versions = [
        {"id": version_id, "version_number": 1, "file_path": SB_FILE},
        {"id": "other", "version_number": 2, "file_path": "sb://library/x/y/z.mp4"},
    ]
    svc = ResourcesService.__new__(ResourcesService)
    svc.repo = _versions_repo(resource, versions)
    # get_versions is called twice: once up front for the "keep at least one"
    # guard, once again (post repo.delete_version) to find the new latest for
    # the switch — after a real delete, only "other" would remain.
    svc.repo.get_versions = AsyncMock(side_effect=[versions, [versions[1]]])

    order: list[str] = []

    async def _fake_set_current_version(rid, version_number, uid):
        order.append("switch")
        return {}

    svc.set_current_version = AsyncMock(side_effect=_fake_set_current_version)

    async def _fake_gc(raw_path, **kwargs):
        order.append("cleanup")
        return "deleted"

    gc = AsyncMock(side_effect=_fake_gc)

    with patch(
        "app.services.library.resources_service.delete_object_if_unreferenced", gc
    ):
        result = await svc.delete_version(resource_id, version_id, "user-1")

    assert result is True
    assert order == ["switch", "cleanup"]
    svc.set_current_version.assert_awaited_once_with(resource_id, 2, "user-1")


async def test_delete_version_legacy_fs_file_path_untouched(tmp_path):
    """A legacy filesystem version's file_path removes its v{n}/ directory
    via shutil, unchanged — never routed through object_gc."""
    resource_id = RID
    version_id = "7000000000000000003"
    rel = f"teams/9/uploads/{resource_id}/v1/clip.mp4"
    real_file = tmp_path / rel
    real_file.parent.mkdir(parents=True, exist_ok=True)
    real_file.write_bytes(b"fake-mp4")

    resource = {"id": resource_id, "current_version": 2}
    versions = [
        {"id": version_id, "version_number": 1, "file_path": rel},
        {"id": "other", "version_number": 2, "file_path": None},
    ]
    svc = ResourcesService.__new__(ResourcesService)
    svc.repo = _versions_repo(resource, versions)
    gc = AsyncMock(return_value="deleted")

    with (
        patch("app.services.library.resources_service.settings") as mock_settings,
        patch(
            "app.services.library.resources_service.delete_object_if_unreferenced",
            gc,
        ),
    ):
        mock_settings.DOWNLOAD_PATH = str(tmp_path)
        await svc.delete_version(resource_id, version_id, "user-1")

    assert not real_file.parent.exists()  # v1/ directory removed
    gc.assert_not_awaited()
