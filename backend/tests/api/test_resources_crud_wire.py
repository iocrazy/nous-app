"""``resources_crud_router`` wire parity after it gained response models.

Each JSON route is driven over real HTTP with rows carrying every column in
the shape the repository really returns, and the body must equal what FastAPI
sent for the bare dict. See ``tests/api/wire_parity.py`` for why.
"""

from __future__ import annotations

import sys

import pytest

from app.main import app
from app.models import (
    Folders,
    ResourceItems,
    Resources,
    ResourceTags,
    ResourceVersions,
    Tags,
)
from app.schemas.resource_responses import ResourceSplitFrame
from app.schemas.resource_rows import (
    FolderRow,
    LibraryResource,
    ResourceItemRow,
    ResourceListItem,
    ResourcePlacement,
    ResourceRow,
    ResourceTagRow,
    ResourceTagTagRow,
    ResourceTagWithTag,
    ResourceVersionRow,
)
from app.services.lrc_parser import parse_lrc
from tests.api import resources_wire_rows
from tests.api.resources_wire_rows import (
    LRC,
    Fake,
    folder_row,
    item_row,
    nulled,
    resource_row,
    resource_tag_row,
    tag_row,
    version_row,
)
from tests.api.wire_parity import assert_wire_unchanged, column_names

# Shared fixtures (auth + guard overrides, the ASGI client), re-exported so
# pytest collects them in this module.
client = resources_wire_rows.client
resources_http_overrides = resources_wire_rows.resources_http_overrides

r = sys.modules["app.api.resources_crud_router"]
RID = "7300000000000000123"


def _library_resource(row: dict) -> dict:
    return {**row, "gallery_count": 3}


def _listing() -> list[dict]:
    full = {**item_row(), "resource": _library_resource(resource_row())}
    bare = {
        **nulled(item_row(), ResourceItemRow),
        "resource": _library_resource(nulled(resource_row(), ResourceRow)),
    }
    return [full, bare]


def _use_repo(monkeypatch, **methods) -> Fake:
    repo = Fake(**methods)
    monkeypatch.setattr(r, "ResourcesRepository", lambda: repo)
    return repo


def _use_service(monkeypatch, repo: Fake | None = None, **methods) -> Fake:
    svc = Fake(**methods)
    svc.repo = repo or Fake()
    monkeypatch.setattr(r, "ResourcesService", lambda: svc)
    return svc


# ── the models pin every column ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("model", "orm", "extra"),
    [
        (ResourceRow, Resources, set()),
        (LibraryResource, Resources, {"gallery_count"}),
        (ResourceSplitFrame, Resources, {"row", "col", "index"}),
        (ResourceItemRow, ResourceItems, set()),
        (ResourceListItem, ResourceItems, {"resource"}),
        (ResourcePlacement, ResourceItems, {"resource"}),
        (ResourceVersionRow, ResourceVersions, set()),
        (FolderRow, Folders, set()),
        (ResourceTagRow, ResourceTags, set()),
        (ResourceTagWithTag, ResourceTags, {"tag"}),
        (ResourceTagTagRow, Tags, set()),
    ],
)
def test_row_model_declares_every_column(model, orm, extra) -> None:
    assert set(model.model_fields) == column_names(orm) | extra


def test_list_embeds_the_gallery_counted_resource() -> None:
    assert ResourceListItem.model_fields["resource"].annotation is LibraryResource


# ── list / trash ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_wire_unchanged(monkeypatch, client) -> None:
    items = _listing()
    _use_repo(monkeypatch, get_resource_items=items)
    resp = await client.get("/api/v1/resources", params={"scope_id": "42"})
    assert_wire_unchanged(resp, {"success": True, "data": items})
    first = resp.json()["data"][0]
    assert first["resource"]["gallery_count"] == 3
    assert first["resource"]["id"] == items[0]["resource"]["id"]  # int, > 2**53


@pytest.mark.asyncio
async def test_trash_wire_unchanged(monkeypatch, client) -> None:
    items = [
        {**item_row(), "resource": resource_row(is_trashed=True)},
        {**nulled(item_row(), ResourceItemRow), "resource": resource_row()},
    ]
    _use_repo(monkeypatch, get_trashed_resources=items)
    resp = await client.get("/api/v1/resources/trash", params={"scope_id": "42"})
    assert_wire_unchanged(resp, {"success": True, "data": items})
    assert "gallery_count" not in resp.json()["data"][0]["resource"]


@pytest.mark.asyncio
async def test_trashed_folders_wire_unchanged(monkeypatch, client) -> None:
    folders = [folder_row(), nulled(folder_row(), FolderRow)]
    _use_repo(monkeypatch, get_trashed_folders=folders)
    resp = await client.get(
        "/api/v1/resources/trash/folders", params={"scope_id": "42"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": folders})


@pytest.mark.asyncio
async def test_batch_transcode_wire_unchanged(monkeypatch, client) -> None:
    import app.services.infra.dbos_orchestrator as orch

    async def _start(*args, **kwargs):
        return None

    monkeypatch.setattr(orch, "start_workflow_routed", _start)
    versions = [{"id": 1, "resource_id": 2, "mime_type": "video/mp4", "file_path": "x"}]
    _use_repo(
        monkeypatch,
        get_untranscoded_video_versions=versions,
        update_version=lambda *a, **k: version_row(),
    )
    resp = await client.post("/api/v1/resources/transcode/batch")
    assert_wire_unchanged(
        resp, {"success": True, "queued": 1, "total_found": 1, "has_more": False}
    )


# ── one resource ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_wire_unchanged(monkeypatch, client) -> None:
    row = resource_row()
    _use_repo(monkeypatch, get_resource_by_id=row)
    resp = await client.get(f"/api/v1/resources/{RID}")
    assert_wire_unchanged(resp, {"success": True, "data": row})
    assert resp.json()["data"]["lyrics_json"]["lines"][1]["line_start_ms"] is None


@pytest.mark.asyncio
async def test_get_nullable_columns_wire_unchanged(monkeypatch, client) -> None:
    row = nulled(resource_row(), ResourceRow)
    _use_repo(monkeypatch, get_resource_by_id=row)
    resp = await client.get(f"/api/v1/resources/{RID}")
    assert_wire_unchanged(resp, {"success": True, "data": row})


@pytest.mark.asyncio
async def test_patch_wire_unchanged(monkeypatch, client) -> None:
    before, after = resource_row(), resource_row(notes="edited")
    _use_repo(monkeypatch, get_resource_by_id=before, update_resource=after)
    resp = await client.patch(f"/api/v1/resources/{RID}", json={"notes": "edited"})
    assert_wire_unchanged(resp, {"success": True, "data": after})


@pytest.mark.asyncio
async def test_patch_noop_returns_the_unchanged_row(monkeypatch, client) -> None:
    row = resource_row()
    _use_repo(monkeypatch, get_resource_by_id=row)
    resp = await client.patch(f"/api/v1/resources/{RID}", json={})
    assert_wire_unchanged(resp, {"success": True, "data": row})


@pytest.mark.asyncio
async def test_upload_cover_wire_unchanged(monkeypatch, tmp_path, client) -> None:
    import app.services.library.transit_upload as transit
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))

    async def _transit(*, user_id, local_path, relative_path, mime):
        return relative_path

    monkeypatch.setattr(transit, "upload_transit_file", _transit)
    updated = resource_row(cover_image_path="teams/42/uploads/1/v1/cover.png")
    _use_repo(
        monkeypatch,
        get_resource_by_id=resource_row(file_path="teams/42/uploads/1/v1/a.png"),
        update_resource=updated,
    )
    resp = await client.post(
        f"/api/v1/resources/{RID}/cover",
        files={"file": ("c.png", b"png-bytes", "image/png")},
    )
    assert_wire_unchanged(resp, {"success": True, "data": updated})


@pytest.mark.asyncio
async def test_upload_lyrics_wire_unchanged(monkeypatch, client) -> None:
    updated = resource_row()
    _use_repo(monkeypatch, get_resource_by_id=resource_row(), update_resource=updated)
    resp = await client.post(
        f"/api/v1/resources/{RID}/lyrics",
        files={"file": ("song.lrc", LRC.encode(), "text/plain")},
    )
    expected = {"lyrics_json": parse_lrc(LRC), "resource": updated}
    assert_wire_unchanged(resp, {"success": True, "data": expected})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    # The last one is a malformed legacy value: it must read back, not 500.
    [parse_lrc(LRC), None, {"lrc": "[00:01.00]x", "lines": "not-a-list"}],
)
async def test_get_lyrics_wire_unchanged(monkeypatch, client, stored) -> None:
    _use_repo(monkeypatch, get_resource_by_id=resource_row(lyrics_json=stored))
    resp = await client.get(f"/api/v1/resources/{RID}/lyrics")
    assert_wire_unchanged(resp, {"success": True, "data": stored})


@pytest.mark.asyncio
async def test_chorus_wire_unchanged(monkeypatch, client) -> None:
    audio = resource_row(source_type="upload", mime_type="audio/mpeg")
    updated = {**audio, "chorus_start_ms": 42000}
    _use_repo(monkeypatch, get_resource_by_id=audio, update_resource=updated)
    resp = await client.put(
        f"/api/v1/resources/{RID}/chorus", json={"chorus_start_ms": 42000}
    )
    assert_wire_unchanged(resp, {"success": True, "data": updated})


# ── trash / delete / restore / move ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["by-platform-id/p1", "by-media-id/9"])
@pytest.mark.parametrize(
    ("query", "message"),
    [
        ({}, "Resource moved to trash"),
        ({"scope_id": "42"}, "Resource removed from team library"),
    ],
)
async def test_trash_by_external_id_wire_unchanged(
    monkeypatch, client, path, query, message
) -> None:
    repo = Fake(
        get_resource_by_platform_id=resource_row(),
        get_resource_by_media_id=resource_row(),
    )
    _use_service(
        monkeypatch,
        repo,
        remove_from_library=True,
        trash_resource=resource_row(),
    )
    resp = await client.post(f"/api/v1/resources/{path}/trash", params=query)
    assert_wire_unchanged(resp, {"success": True, "message": message})


@pytest.mark.asyncio
async def test_unlink_by_platform_id_wire_unchanged(monkeypatch, client) -> None:
    repo = Fake(
        get_resource_by_platform_id=resource_row(),
        get_resource_item=item_row(),
        delete_resource_item=True,
    )
    _use_service(monkeypatch, repo)
    resp = await client.delete(
        "/api/v1/resources/by-platform-id/p1", params={"scope_id": "42"}
    )
    assert_wire_unchanged(
        resp, {"success": True, "message": "Resource unlinked from library"}
    )


@pytest.mark.asyncio
async def test_delete_wire_unchanged(monkeypatch, client) -> None:
    _use_service(monkeypatch, remove_from_library=True)
    resp = await client.delete(f"/api/v1/resources/{RID}", params={"scope_id": "42"})
    assert_wire_unchanged(
        resp, {"success": True, "message": "Resource removed from library"}
    )


@pytest.mark.asyncio
async def test_restore_wire_unchanged(monkeypatch, client) -> None:
    row = resource_row(is_trashed=False, trashed_at=None)
    _use_service(monkeypatch, restore_resource=row)
    resp = await client.post(f"/api/v1/resources/{RID}/restore")
    assert_wire_unchanged(resp, {"success": True, "data": row})


@pytest.mark.asyncio
async def test_permanent_delete_wire_unchanged(monkeypatch, client) -> None:
    _use_service(monkeypatch, permanent_delete=True)
    resp = await client.delete(f"/api/v1/resources/{RID}/permanent")
    assert_wire_unchanged(
        resp, {"success": True, "message": "Resource permanently deleted"}
    )


@pytest.mark.asyncio
async def test_move_wire_unchanged(monkeypatch, client) -> None:
    moved = item_row()
    _use_service(monkeypatch, move_resource=moved)
    resp = await client.post(
        f"/api/v1/resources/{RID}/move", json={"scope_id": "42", "folder_id": "9"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": moved})


# ── tags / split ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tags_wire_unchanged(monkeypatch, client) -> None:
    tags = [
        {**resource_tag_row(), "tag": tag_row()},
        {
            **nulled(resource_tag_row(), ResourceTagRow),
            "tag": nulled(tag_row(), ResourceTagTagRow),
        },
    ]
    _use_repo(monkeypatch, get_resource_tags=tags)
    resp = await client.get(f"/api/v1/resources/{RID}/tags")
    assert_wire_unchanged(resp, {"success": True, "data": tags})


@pytest.mark.asyncio
async def test_add_tag_wire_unchanged(monkeypatch, client) -> None:
    junction = resource_tag_row()
    _use_repo(monkeypatch, get_resource_by_id=resource_row(), add_resource_tag=junction)
    resp = await client.post(f"/api/v1/resources/{RID}/tags", json={"tag_id": "5"})
    assert_wire_unchanged(resp, {"success": True, "data": junction})


@pytest.mark.asyncio
async def test_remove_tag_wire_unchanged(monkeypatch, client) -> None:
    _use_repo(monkeypatch, remove_resource_tag=True)
    resp = await client.delete(f"/api/v1/resources/{RID}/tags/5")
    assert_wire_unchanged(resp, {"success": True, "message": "Tag removed"})


@pytest.mark.asyncio
async def test_split_wire_unchanged(monkeypatch, client) -> None:
    import app.services.canvas.split_derive_service as split

    frames = [
        {**resource_row(), "row": 0, "col": 0, "index": 0},
        {**resource_row(), "row": 0, "col": 1, "index": 1},
    ]

    async def _derive(**kwargs):
        return split.SplitDeriveResult(frames=frames)

    monkeypatch.setattr(split, "derive_split_resource", _derive)
    resp = await client.post(
        f"/api/v1/resources/{RID}/split", json={"rows": 1, "cols": 2}
    )
    assert_wire_unchanged(resp, {"success": True, "data": {"frames": frames}})


# ── binary routes advertise no JSON body ────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/resources/{resource_id}/file", "get"),
        ("/api/v1/resources/{resource_id}/cover", "get"),
        ("/api/v1/resources/{resource_id}/preview-sprite", "get"),
        ("/api/v1/resources/{resource_id}/versions/{version_id}/file", "get"),
        ("/api/v1/resources/{resource_id}/versions/{version_id}/hls/{path}", "get"),
        ("/api/v1/resources/export/training-set", "post"),
    ],
)
def test_binary_routes_declare_no_json_body(path, method) -> None:
    content = app.openapi()["paths"][path][method]["responses"]["200"]["content"]
    assert "application/json" not in content
    for media in content.values():
        assert media["schema"] == {"type": "string", "format": "binary"}
