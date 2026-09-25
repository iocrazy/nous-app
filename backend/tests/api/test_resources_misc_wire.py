"""Wire parity of the smaller ``/resources`` routers: ai, gallery, upload,
search and save-as-asset.

Same method as ``test_resources_crud_wire.py``.
"""

from __future__ import annotations

import datetime as dt
import sys

import pytest

from app.models import Resources
from app.models.media import AiTaskStatus
from app.schemas.generated import GeneratedItem
from app.schemas.resource_responses import (
    ResourceDuplicateCandidate,
    ResourceSearchCounts,
)
from app.schemas.resource_rows import ResourceRow
from app.services.library.gallery_mime import GALLERY_MIME
from tests.api import resources_wire_rows
from tests.api.resources_wire_rows import (
    Fake,
    nulled,
    resource_row,
)
from tests.api.wire_parity import SAMPLE_TS, assert_wire_unchanged, sample_row

# Shared fixtures (auth + guard overrides, the ASGI client), re-exported so
# pytest collects them in this module.
client = resources_wire_rows.client
resources_http_overrides = resources_wire_rows.resources_http_overrides

air = sys.modules["app.api.resources_ai_router"]
gar = sys.modules["app.api.resources_gallery_router"]
upr = sys.modules["app.api.resources_upload_router"]
sr = sys.modules["app.api.resources_search_router"]
asr = sys.modules["app.api.resources_assets_router"]
repo_mod = sys.modules["app.repositories.resources_repository"]
RID = "7300000000000000123"


def _patch_workflow_start(monkeypatch) -> list[dict]:
    import app.services.infra.dbos_orchestrator as orch

    calls: list[dict] = []

    async def _start(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(orch, "start_workflow_routed", _start)
    return calls


# ── ai ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True])
async def test_translate_wire_unchanged(monkeypatch, client, nulls) -> None:
    updated = resource_row(gen_prompt="a cat", gen_prompt_zh="一只猫")
    if nulls:
        updated = nulled(updated, ResourceRow)
    repo = Fake(get_resource_by_id=resource_row(), update_resource=updated)
    monkeypatch.setattr(air, "ResourcesRepository", lambda: repo)
    monkeypatch.setattr(air, "build_translate_plan", lambda *a: {"x": "y"})

    async def _translate(*args, **kwargs):
        return {"gen_prompt_zh": "一只猫"}

    monkeypatch.setattr(air, "translate_fields", _translate)
    resp = await client.post(
        f"/api/v1/resources/{RID}/gen-prompt/translate", json={"target_lang": "zh"}
    )
    keys = (
        "gen_prompt",
        "gen_prompt_zh",
        "gen_prompt_negative",
        "gen_prompt_negative_zh",
    )
    expected = {k: updated.get(k) for k in keys}
    assert_wire_unchanged(resp, {"success": True, "data": expected})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["gen-prompt/generate", "classify"])
async def test_single_asset_ai_wire_unchanged(monkeypatch, client, path) -> None:
    repo = Fake(get_resource_by_id=resource_row())
    monkeypatch.setattr(air, "ResourcesRepository", lambda: repo)

    async def _no_gate(*args):
        return None

    async def _dispatch(*args):
        return "wf-123"

    monkeypatch.setattr(air, "_gate_reason", _no_gate)
    monkeypatch.setattr(air, "_dispatch_asset_ai", _dispatch)
    resp = await client.post(f"/api/v1/resources/{RID}/{path}")
    assert_wire_unchanged(resp, {"success": True, "task_id": "wf-123"})


@pytest.mark.asyncio
async def test_slide_prompt_wire_unchanged(monkeypatch, client) -> None:
    import app.repositories.media_repository as media_repo
    import app.services.infra.unified_task_manager as utm
    import app.services.media.slide_paths as slides

    repo = Fake(get_resource_by_id=resource_row(media_id=99))
    monkeypatch.setattr(air, "ResourcesRepository", lambda: repo)
    monkeypatch.setattr(
        media_repo, "MediaRepository", lambda: Fake(get_by_id={"download_path": "d"})
    )
    monkeypatch.setattr(slides, "resolve_slide_source", Fake(f=None).f)
    monkeypatch.setattr(utm, "get_task_manager", lambda: Fake(create="t"))
    calls = _patch_workflow_start(monkeypatch)
    resp = await client.post(f"/api/v1/resources/{RID}/slides/01.jpg/generate-prompt")
    task_id = calls[0]["workflow_id"]
    assert_wire_unchanged(resp, {"success": True, "task_id": task_id})


@pytest.mark.asyncio
async def test_batch_ai_wire_unchanged(monkeypatch, client) -> None:
    repo = Fake(get_resource_by_id=resource_row())
    monkeypatch.setattr(air, "ResourcesRepository", lambda: repo)

    async def _gate(operation, resource):
        return None

    async def _dispatch(resource, user_id, operation):
        if resource is None:
            raise RuntimeError("boom")
        return "wf-1"

    monkeypatch.setattr(air, "_gate_reason", _gate)
    monkeypatch.setattr(air, "_dispatch_asset_ai", _dispatch)
    resp = await client.post(
        "/api/v1/resources/ai/batch",
        json={"resource_ids": ["1", "2", "1"], "operation": "caption"},
    )
    expected = {
        "success": True,
        "dispatched": [
            {"resource_id": "1", "task_id": "wf-1"},
            {"resource_id": "2", "task_id": "wf-1"},
        ],
        "skipped": [],
    }
    assert_wire_unchanged(resp, expected)


@pytest.mark.asyncio
async def test_batch_ai_skipped_wire_unchanged(monkeypatch, client) -> None:
    repo = Fake(get_resource_by_id=None)
    monkeypatch.setattr(air, "ResourcesRepository", lambda: repo)
    resp = await client.post(
        "/api/v1/resources/ai/batch",
        json={"resource_ids": ["1"], "operation": "classify"},
    )
    expected = {
        "success": True,
        "dispatched": [],
        "skipped": [{"resource_id": "1", "reason": "Resource not found"}],
    }
    assert_wire_unchanged(resp, expected)


# ── gallery ─────────────────────────────────────────────────────────────────


def _gallery_children() -> list[dict]:
    return [
        {
            "id": 7300000000000000201,
            "filename": "a.png",
            "thumbnail_path": "t/a.jpg",
            "position": 0,
        },
        {
            "id": 7300000000000000202,
            "filename": "b.png",
            "thumbnail_path": None,
            "position": 1,
        },
    ]


def _use_gallery(monkeypatch, **methods) -> Fake:
    repo = Fake(**methods)
    monkeypatch.setattr(gar, "ResourcesRepository", lambda: repo)

    async def _access(*args):
        return True

    monkeypatch.setattr(gar, "check_media_access", _access)
    return repo


@pytest.mark.asyncio
async def test_create_gallery_wire_unchanged(monkeypatch, client) -> None:
    gallery = resource_row(file_type="gallery", mime_type=GALLERY_MIME)
    _use_gallery(monkeypatch, create_resource=gallery, create_resource_item={})
    resp = await client.post(
        "/api/v1/resources/galleries", params={"scope_id": "42", "filename": "Set"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": gallery})


@pytest.mark.asyncio
async def test_gallery_membership_wire_unchanged(monkeypatch, client) -> None:
    membership = {
        "child_image_ids": ["7300000000000000201"],
        "gallery_counts": {"7300000000000000200": 2},
    }
    _use_gallery(monkeypatch, get_scope_gallery_membership=membership)
    resp = await client.get(
        "/api/v1/resources/gallery-membership", params={"scope_id": "42"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": membership})


@pytest.mark.asyncio
async def test_set_gallery_items_wire_unchanged(monkeypatch, client) -> None:
    children = _gallery_children()
    gallery = resource_row(mime_type=GALLERY_MIME)
    _use_gallery(
        monkeypatch,
        get_resource_by_id=gallery,
        validate_scope_image_ids={"1", "2"},
        set_gallery_items=[],
        update_resource=gallery,
        get_gallery_items=children,
    )
    resp = await client.put(
        f"/api/v1/resources/{RID}/gallery-items",
        params={"scope_id": "42"},
        json={"image_ids": ["1", "2"]},
    )
    assert_wire_unchanged(resp, {"success": True, "data": children})


@pytest.mark.asyncio
async def test_list_gallery_items_wire_unchanged(monkeypatch, client) -> None:
    children = _gallery_children()
    _use_gallery(
        monkeypatch,
        get_resource_by_id=resource_row(mime_type=GALLERY_MIME),
        get_gallery_items=children,
    )
    resp = await client.get(f"/api/v1/resources/{RID}/gallery-items")
    assert_wire_unchanged(resp, {"success": True, "data": children})


# ── upload / duplicates / permissions ───────────────────────────────────────

# ``find_by_hash``'s projection, in its select order.
_FIND_BY_HASH = (
    "id",
    "filename",
    "file_type",
    "mime_type",
    "file_size_bytes",
    "thumbnail_path",
    "cover_image_path",
    "created_at",
)


def test_duplicate_candidate_is_the_find_by_hash_projection() -> None:
    assert set(ResourceDuplicateCandidate.model_fields) == set(_FIND_BY_HASH)


@pytest.mark.asyncio
async def test_permissions_wire_unchanged(monkeypatch, client) -> None:
    result = {"role": "editor", "capabilities": ["read", "write"]}
    monkeypatch.setattr(
        upr, "PermissionService", lambda: Fake(get_effective_role=result)
    )
    resp = await client.get(
        "/api/v1/resources/permissions",
        params={"object_type": "folder", "object_id": "1", "team_id": "42"},
    )
    assert_wire_unchanged(resp, {"success": True, "data": result})


@pytest.mark.asyncio
@pytest.mark.parametrize("size_matches", [True, False])
async def test_check_duplicate_wire_unchanged(
    monkeypatch, client, size_matches
) -> None:
    match = repo_mod._rest_parity(sample_row(Resources, only=_FIND_BY_HASH))
    size = match["file_size_bytes"] if size_matches else 1
    monkeypatch.setattr(upr, "ResourcesRepository", lambda: Fake(find_by_hash=[match]))
    resp = await client.get(
        "/api/v1/resources/check-duplicate",
        params={"file_hash": "a" * 64, "file_size": size},
    )
    expected = {
        "duplicate": size_matches,
        "existing": match if size_matches else None,
    }
    assert_wire_unchanged(resp, expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("already", [True, False])
async def test_link_existing_wire_unchanged(monkeypatch, client, already) -> None:
    row = resource_row()
    repo = Fake(
        get_resource_by_id=row,
        find_resource_item={"id": 1} if already else None,
        create_resource_item={},
    )
    monkeypatch.setattr(upr, "ResourcesRepository", lambda: repo)
    resp = await client.post(
        "/api/v1/resources/link-existing",
        params={"resource_id": RID, "scope_id": "42"},
    )
    expected = {"success": True, "data": row}
    if already:
        expected["already_linked"] = True
    assert_wire_unchanged(resp, expected)
    assert ("already_linked" in resp.json()) is already


@pytest.mark.asyncio
async def test_upload_wire_unchanged(monkeypatch, client) -> None:
    import app.services.infra.unified_task_manager as utm

    created = resource_row()
    monkeypatch.setattr(
        utm,
        "get_task_manager",
        lambda: Fake(create="task-1", start=None, complete=None, fail=None),
    )
    _patch_workflow_start(monkeypatch)
    monkeypatch.setattr(upr, "ResourcesService", lambda: Fake(upload_resource=created))
    resp = await client.post(
        "/api/v1/resources/upload",
        params={"scope_id": "42"},
        files={"file": ("a.png", b"png", "image/png")},
    )
    assert_wire_unchanged(resp, {"success": True, "data": created})


# ── search ──────────────────────────────────────────────────────────────────


def _picker_row(**over) -> dict:
    """``list_accessible_for_user``'s row: raw ``.mappings()``, no
    ``_rest_parity`` — so ``updated_at`` is a native datetime and the two
    status columns are enum members."""
    row = {
        "id": RID,
        "name": "clip.mp4",
        "mime": "video/mp4",
        "size": 7300000000000000999,
        "updated_at": SAMPLE_TS,
        "thumbnail_path": "t/x.jpg",
        "cover_image_path": None,
        "media_id": 7300000000000000777,
        "transcript_status": AiTaskStatus.COMPLETED,
        "summary_status": AiTaskStatus.NONE,
        "scope_id": "42",
        "scope_type": "personal",
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_search_wire_unchanged(monkeypatch, client) -> None:
    rows = [
        _picker_row(),
        _picker_row(
            id="9",
            mime=None,
            size=None,
            thumbnail_path=None,
            media_id=None,
            scope_type="team",
        ),
    ]
    counts = {"all": 2, "video": 1, "image": 0, "audio": 0, "pdf": 0, "doc": 1}
    assert set(counts) == set(ResourceSearchCounts.model_fields)
    monkeypatch.setattr(
        sr,
        "ResourcesRepository",
        lambda: Fake(
            list_accessible_for_user=rows, count_accessible_by_kind_for_user=counts
        ),
    )
    statuses = {
        RID: {"transcript_status": "completed", "summary_status": "none"},
        "9": {"transcript_status": None, "summary_status": None},
    }

    async def _effective(by_id):
        return statuses

    monkeypatch.setattr(sr, "effective_ai_statuses", _effective)
    resp = await client.get("/api/v1/resources/search")
    expected_results = [
        {
            "id": RID,
            "name": "clip.mp4",
            "kind": "video",
            "mime": "video/mp4",
            "size": 7300000000000000999,
            "scope": {"type": "personal", "id": "42"},
            "updated_at": SAMPLE_TS,
            "thumbnail_url": f"/api/v1/resources/{RID}/cover",
            "transcript_status": "completed",
            "summary_status": "none",
        },
        {
            "id": "9",
            "name": "clip.mp4",
            "kind": "doc",
            "mime": None,
            "size": None,
            "scope": {"type": "team", "id": "42"},
            "updated_at": SAMPLE_TS,
            "thumbnail_url": None,
            "transcript_status": None,
            "summary_status": None,
        },
    ]
    assert_wire_unchanged(
        resp, {"results": expected_results, "counts": counts, "next_cursor": None}
    )
    assert resp.json()["results"][0]["updated_at"].endswith("+00:00")


# ── save-as-asset ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_as_asset_wire_unchanged(monkeypatch, client) -> None:
    generation = {
        "id": "800000000000000001",
        "scope_id": "42",
        "media_kind": "image",
        "mime": "image/png",
        "prompt": None,
        "model": None,
        "provider": None,
        "origin_kind": "library_upload",
        "canvas_id": None,
        "node_id": None,
        "created_at": dt.datetime(2026, 9, 4, 12, 0, 0, 123456, tzinfo=dt.timezone.utc),
        "promoted_resource_id": RID,
        "review_state": "in_assets",
        "source_asset_id": None,
        "source": {"kind": "library_upload", "label": "My Uploads"},
        "title": "harbour",
    }
    out = {
        "generation": generation,
        "asset_id": "700000000000000001",
        "resource_id": RID,
        "generated_id": "800000000000000001",
    }

    async def _gate(scope_id, auth):
        return 42

    monkeypatch.setattr(asr, "_gate", _gate)
    monkeypatch.setattr(asr, "_service", lambda: Fake(save_resource_as_asset=out))
    resp = await client.post(
        f"/api/v1/resources/{RID}/save-as-asset",
        params={"scope_id": "42"},
        json={"asset_id": "700000000000000001", "slot": "sheet"},
    )
    raw = {
        **out,
        "generation": GeneratedItem.model_validate(generation).model_dump(mode="json"),
    }
    assert_wire_unchanged(resp, {"success": True, "data": raw}, status=201)
