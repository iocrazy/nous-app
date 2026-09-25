"""``resources_folders_router`` + ``resources_versions_router`` wire parity.

Same method as ``test_resources_crud_wire.py``: real HTTP, rows built from the
ORM through the repository's own converters, body == ``jsonable_encoder`` of
the dict the handler returned before it had a response model.
"""

from __future__ import annotations

import sys

import pytest

from app.schemas.resource_rows import FolderRow, ResourceItemRow, ResourceVersionRow
from tests.api import resources_wire_rows
from tests.api.resources_wire_rows import (
    Fake,
    folder_row,
    item_row,
    nulled,
    resource_row,
    version_row,
)
from tests.api.wire_parity import assert_wire_unchanged

# Shared fixtures (auth + guard overrides, the ASGI client), re-exported so
# pytest collects them in this module.
client = resources_wire_rows.client
resources_http_overrides = resources_wire_rows.resources_http_overrides

fr = sys.modules["app.api.resources_folders_router"]
vr = sys.modules["app.api.resources_versions_router"]
FID = "7300000000000000555"
RID = "7300000000000000123"


def _smart_folder(**over) -> dict:
    return folder_row(
        is_smart=True,
        is_system=False,
        smart_rules={"operator": "AND", "match": True, "conditions": []},
        **over,
    )


def _use_folders(monkeypatch, svc: Fake | None = None, **methods) -> Fake:
    repo = Fake(**methods)
    monkeypatch.setattr(fr, "ResourcesRepository", lambda: repo)
    monkeypatch.setattr(fr, "ResourcesService", lambda: svc or Fake())

    async def _owner(folder, auth):
        return None

    monkeypatch.setattr(fr, "_verify_folder_ownership_inline", _owner)
    return repo


# ── smart folders ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_smart_folder_wire_unchanged(monkeypatch, client) -> None:
    created = _smart_folder()
    _use_folders(monkeypatch, create_smart_folder=created)
    resp = await client.post(
        "/api/v1/resources/smart-folders",
        json={
            "name": "Recent",
            "scope_id": "42",
            "rules": {"operator": "AND", "conditions": []},
        },
    )
    assert_wire_unchanged(resp, {"success": True, "data": created})


@pytest.mark.asyncio
async def test_list_smart_folders_wire_unchanged(monkeypatch, client) -> None:
    folders = [_smart_folder(), nulled(_smart_folder(), FolderRow)]
    _use_folders(monkeypatch, get_smart_folders=folders)
    resp = await client.get(
        "/api/v1/resources/smart-folders", params={"scope_id": "42"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": folders})


@pytest.mark.asyncio
async def test_update_smart_folder_wire_unchanged(monkeypatch, client) -> None:
    updated = _smart_folder(name="Renamed")
    _use_folders(monkeypatch, get_folder_by_id=_smart_folder(), update_folder=updated)
    resp = await client.patch(
        f"/api/v1/resources/smart-folders/{FID}", json={"name": "Renamed"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": updated})


@pytest.mark.asyncio
async def test_delete_smart_folder_wire_unchanged(monkeypatch, client) -> None:
    _use_folders(monkeypatch, get_folder_by_id=_smart_folder(), delete_folder=True)
    resp = await client.delete(f"/api/v1/resources/smart-folders/{FID}")
    assert_wire_unchanged(resp, {"success": True, "message": "Smart folder deleted"})


@pytest.mark.asyncio
async def test_smart_folder_results_wire_unchanged(monkeypatch, client) -> None:
    items = [
        {**item_row(), "resource": resource_row()},
        {**nulled(item_row(), ResourceItemRow), "resource": resource_row()},
    ]
    folder = _smart_folder()
    folder["smart_rules"] = {
        "operator": "AND",
        "match": True,
        "conditions": [{"field": "rating", "op": "gte", "value": "3"}],
    }
    _use_folders(monkeypatch, get_folder_by_id=folder, execute_smart_rules=items)
    resp = await client.get(
        f"/api/v1/resources/smart-folders/{FID}/results", params={"scope_id": "42"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": items})


# ── regular folders ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_folders_wire_unchanged(monkeypatch, client) -> None:
    folders = [folder_row(), nulled(folder_row(), FolderRow)]
    _use_folders(monkeypatch, get_folders=folders)
    resp = await client.get("/api/v1/resources/folders/list", params={"scope_id": "42"})
    assert_wire_unchanged(resp, {"success": True, "data": folders})


@pytest.mark.asyncio
async def test_create_folder_wire_unchanged(monkeypatch, client) -> None:
    created = folder_row(is_system=False)
    _use_folders(monkeypatch, create_folder=created)
    resp = await client.post(
        "/api/v1/resources/folders", json={"name": "Clips", "scope_id": "42"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": created})


@pytest.mark.asyncio
async def test_update_folder_wire_unchanged(monkeypatch, client) -> None:
    updated = folder_row(is_system=False, name="Renamed")
    _use_folders(
        monkeypatch,
        get_folder_by_id=folder_row(is_system=False),
        update_folder=updated,
    )
    resp = await client.patch(
        f"/api/v1/resources/folders/{FID}", json={"name": "Renamed"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": updated})


@pytest.mark.asyncio
async def test_content_count_wire_unchanged(monkeypatch, client) -> None:
    counts = {"resource_count": 12, "subfolder_count": 2}
    _use_folders(
        monkeypatch,
        get_folder_by_id=folder_row(),
        get_descendant_folder_ids=["1", "2"],
        count_folder_contents=counts,
    )
    resp = await client.get(f"/api/v1/resources/folders/{FID}/content-count")
    assert_wire_unchanged(resp, {"success": True, "data": counts})


@pytest.mark.asyncio
async def test_trash_folder_wire_unchanged(monkeypatch, client) -> None:
    result = {"trashed_folders": 3, "trashed_resources": 8}
    _use_folders(
        monkeypatch,
        get_folder_by_id=folder_row(is_system=False),
        trash_folder_cascade=result,
    )
    resp = await client.post(f"/api/v1/resources/folders/{FID}/trash")
    assert_wire_unchanged(resp, {"success": True, "data": result})


@pytest.mark.asyncio
async def test_restore_folder_wire_unchanged(monkeypatch, client) -> None:
    result = {"restored_folders": 3, "restored_resources": 8}
    _use_folders(
        monkeypatch, get_folder_by_id=folder_row(), restore_folder_cascade=result
    )
    resp = await client.post(f"/api/v1/resources/folders/{FID}/restore")
    assert_wire_unchanged(resp, {"success": True, "data": result})


@pytest.mark.asyncio
async def test_delete_folder_wire_unchanged(monkeypatch, client) -> None:
    result = {"deleted_folders": 2, "deleted_resources": 5}
    svc = Fake(permanent_delete_folder=result)
    _use_folders(monkeypatch, svc, get_folder_by_id=folder_row(is_system=False))
    resp = await client.delete(f"/api/v1/resources/folders/{FID}")
    assert_wire_unchanged(
        resp,
        {"success": True, "message": "Folder permanently deleted", "data": result},
    )


# ── versions ────────────────────────────────────────────────────────────────


def _use_versions(monkeypatch, svc: Fake | None = None, **methods) -> Fake:
    import app.services.infra.dbos_orchestrator as orch

    async def _start(*args, **kwargs):
        return None

    monkeypatch.setattr(orch, "start_workflow_routed", _start)
    repo = Fake(**methods)
    monkeypatch.setattr(vr, "ResourcesRepository", lambda: repo)
    monkeypatch.setattr(vr, "ResourcesService", lambda: svc or Fake())
    return repo


@pytest.mark.asyncio
async def test_list_versions_wire_unchanged(monkeypatch, client) -> None:
    versions = [version_row(), nulled(version_row(), ResourceVersionRow)]
    _use_versions(monkeypatch, get_resource_by_id=resource_row(), get_versions=versions)
    resp = await client.get(f"/api/v1/resources/{RID}/versions")
    assert_wire_unchanged(resp, {"success": True, "data": versions})


@pytest.mark.asyncio
async def test_upload_version_wire_unchanged(monkeypatch, client) -> None:
    created = version_row(mime_type="video/mp4", version_number=2)
    _use_versions(monkeypatch, Fake(upload_new_version=created))
    resp = await client.post(
        f"/api/v1/resources/{RID}/versions",
        files={"file": ("b.mp4", b"bytes", "video/mp4")},
    )
    assert_wire_unchanged(resp, {"success": True, "data": created})


@pytest.mark.asyncio
async def test_overwrite_version_wire_unchanged(monkeypatch, client) -> None:
    updated = version_row()
    _use_versions(monkeypatch, Fake(overwrite_version_content=updated))
    resp = await client.put(
        f"/api/v1/resources/{RID}/versions/9/content",
        files={"file": ("a.txt", b"text", "text/plain")},
    )
    assert_wire_unchanged(resp, {"success": True, "data": updated})


@pytest.mark.asyncio
async def test_set_current_version_wire_unchanged(monkeypatch, client) -> None:
    version = nulled(version_row(), ResourceVersionRow)
    _use_versions(monkeypatch, Fake(set_current_version=version))
    resp = await client.post(f"/api/v1/resources/{RID}/versions/2/set-current")
    assert_wire_unchanged(resp, {"success": True, "data": version})


@pytest.mark.asyncio
async def test_delete_version_wire_unchanged(monkeypatch, client) -> None:
    _use_versions(monkeypatch, Fake(delete_version=True))
    resp = await client.delete(f"/api/v1/resources/{RID}/versions/9")
    assert_wire_unchanged(resp, {"success": True, "message": "Version deleted"})


@pytest.mark.asyncio
async def test_retry_transcode_wire_unchanged(monkeypatch, client) -> None:
    video = version_row(resource_id=int(RID), mime_type="video/mp4")
    _use_versions(monkeypatch, get_version_by_id=video, update_version=video)
    resp = await client.post(f"/api/v1/resources/{RID}/versions/9/transcode")
    assert_wire_unchanged(resp, {"success": True, "message": "Transcoding queued"})
