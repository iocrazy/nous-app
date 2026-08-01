"""Admin storage 只读端点的数据逻辑测试(monkeypatch db_engine,不起 HTTP)。

House convention (see test_admin_storage_migration_router.py): reach the
submodule via importlib, not ``from app.api.admin import storage_router``.
``app/api/admin/__init__.py`` does ``from .storage_router import router as
storage_router`` to mount the router, which REBINDS the package attribute
``storage_router`` to the APIRouter instance — shadowing the submodule of the
same name. A plain ``from app.api.admin import storage_router`` therefore
resolves to the router object (no ``db_engine``/``_fetch_*`` attributes),
not this module.
"""

import importlib
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

sr = importlib.import_module("app.api.admin.storage_router")


def _auth():
    auth = MagicMock()
    auth.user_id = "admin-1"
    return auth


def _request(host="10.0.0.9"):
    request = MagicMock()
    request.client.host = host
    return request


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build a real httpx.HTTPStatusError the way ObjectStore.get_size's
    ``resp.raise_for_status()`` would (see test_storage_audit_workflow.py's
    identically-named helper) — anchors the uncertain/missing split on the
    real exception shape rather than a stand-in that could hide a
    404-vs-other-error classification bug."""
    request = httpx.Request("HEAD", "http://store.internal/x")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code}", request=request, response=response)


@pytest.mark.asyncio
async def test_fetch_stats_shapes(monkeypatch):
    async def fake_fetch_one(sql, params=None):
        s = " ".join(sql.split()).lower()
        if "sum(storage_size)" in s:
            return {"count": 1079, "size_bytes": 36507222016}
        # Precise match on the dedicated hls_ready COUNT ("hls_path LIKE
        # 'sb://%'") — NOT a bare "hls_path" substring, which would also
        # match the fs_residue query's "hls_path NOT LIKE 'sb://%'"
        # condition (that query legitimately checks the same column for
        # filesystem residue, so it necessarily mentions the column name
        # too; only the LIKE/NOT LIKE distinction tells the two apart).
        if "hls_path like 'sb://%'" in s:
            return {"n": 108}
        if "orphan" in s or "r.id is null" in s:
            return {"n": 0}
        return {"n": 0}  # fs_residue

    async def fake_latest_audit():
        return {
            "status": "completed",
            "scanned": 3187,
            "errors": 0,
            "scanned_at": "2026-07-31T04:00:00Z",
            "missing": [
                {
                    "key": "derived/9/x.webp",
                    "kind": "thumbnail",
                    "media_id": None,
                    "resource_id": "9",
                }
            ],
        }

    monkeypatch.setattr(sr.db_engine, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(sr, "_fetch_latest_audit", fake_latest_audit)
    out = await sr._fetch_stats()
    assert out["videos"] == {"count": 1079, "size_bytes": 36507222016}
    assert out["fs_residue"] == 0 and out["orphans"] == 0 and out["hls_ready"] == 108
    assert out["broken"] == 1
    assert out["last_scan"]["scanned"] == 3187


@pytest.mark.asyncio
async def test_media_status_derivation(monkeypatch):
    rows = [
        # ok: 视频 sb://,cover/thumb/hls 齐
        {
            "media_id": 1,
            "video_key": "sb://library/t5/aa/bb/x.mp4",
            "video_size": 10,
            "video_download_status": "completed",
            "cover_path": "sb://library/derived/1/c.jpg",
            "thumbnail_path": "sb://library/derived/1/t.webp",
            "hls_path": "sb://library/hls/1/2/master.m3u8",
            "res_file_path": "sb://library/t5/aa/bb/x.mp4",
            "res_cover_image": None,
            "rv_file_path": "sb://library/t5/aa/bb/x.mp4",
            "scope_id": 5,
        },
        # no_video: download_path NULL
        {
            "media_id": 2,
            "video_key": None,
            "video_size": None,
            "video_download_status": "failed",
            "cover_path": None,
            "thumbnail_path": None,
            "hls_path": None,
            "res_file_path": None,
            "res_cover_image": None,
            "rv_file_path": None,
            "scope_id": None,
        },
        # fs_residue: 任一列非 sb:// 的路径
        {
            "media_id": 3,
            "video_key": "global/resources/web/x/video.mp4",
            "video_size": 5,
            "video_download_status": "completed",
            "cover_path": None,
            "thumbnail_path": None,
            "hls_path": None,
            "res_file_path": None,
            "res_cover_image": None,
            "rv_file_path": None,
            "scope_id": None,
        },
    ]

    async def fake_fetch_all(sql, params=None):
        return rows

    monkeypatch.setattr(sr.db_engine, "fetch_all", fake_fetch_all)
    out = await sr._fetch_media_status([1, 2, 3])
    by = {r["media_id"]: r for r in out}
    assert (
        by["1"]["storage_status"] == "ok" and by["1"]["cover_ok"] and by["1"]["hls_ok"]
    )
    assert by["2"]["storage_status"] == "no_video"
    assert by["3"]["storage_status"] == "fs_residue"


@pytest.mark.asyncio
async def test_media_detail_assets(monkeypatch):
    async def fake_fetch_one(sql, params=None):
        return {
            "media_id": 7,
            "download_path": "sb://library/t5/aa/bb/v.mp4",
            "storage_size": 197_000_000,
            "cover_download_path": "sb://library/derived/70/cover.jpg",
            "resource_id": 70,
            "thumbnail_path": "sb://library/derived/70/thumbnail.webp",
            "hls_path": "sb://library/hls/70/71/master.m3u8",
            "scope_id": 5,
        }

    monkeypatch.setattr(sr.db_engine, "fetch_one", fake_fetch_one)
    out = await sr._fetch_media_detail(7)
    kinds = {a["kind"]: a for a in out["assets"]}
    assert (
        kinds["video"]["key"].endswith("v.mp4")
        and kinds["video"]["size_bytes"] == 197_000_000
    )
    assert kinds["sprite"]["key"] == "sb://library/derived/70/preview_sprite.jpg"
    assert kinds["sprite"]["present_in_db"] is False
    assert kinds["hls"]["key"].endswith("master.m3u8")
    assert out["scope_id"] == "5"


@pytest.mark.asyncio
async def test_media_status_rejects_non_integer_ids():
    """Task 1 ledger item: media_ids boundary validation had no test —
    non-numeric tokens must 422, not raise an unhandled ValueError."""
    with pytest.raises(HTTPException) as exc_info:
        await sr.get_media_status(auth=None, media_ids="1,abc,3")
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_media_status_rejects_too_many_ids():
    """Task 1 ledger item: the >200 ids branch had no test."""
    media_ids = ",".join(str(i) for i in range(1, 202))  # 201 ids
    with pytest.raises(HTTPException) as exc_info:
        await sr.get_media_status(auth=None, media_ids=media_ids)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_verify_keys_exists_missing_uncertain():
    """_verify_keys must classify via the same present/missing/error
    semantics as storage_audit._probe_one (404 → missing, any other
    failure → uncertain/None, never conflated with missing)."""

    class FakeStore:
        async def get_size(self, key):
            if key.endswith("gone.jpg"):
                raise _http_status_error(404)
            if key.endswith("boom.webp"):
                raise _http_status_error(500)
            return 123

    assets = [
        {"kind": "video", "key": "sb://library/t5/aa/v.mp4"},
        {"kind": "cover", "key": "sb://library/derived/1/gone.jpg"},
        {"kind": "thumbnail", "key": "sb://library/derived/1/boom.webp"},
        {"kind": "hls", "key": None},  # 无 key 跳过
    ]
    out = await sr._verify_keys(FakeStore(), assets)
    by = {r["kind"]: r for r in out}
    assert by["video"]["exists"] is True
    assert by["cover"]["exists"] is False
    assert by["thumbnail"]["exists"] is None  # 异常 → 不确定
    assert "hls" not in by


@pytest.mark.asyncio
async def test_deep_verify_dedups_running(monkeypatch):
    """task_tracking's PK column is dbos_workflow_id, not task_id (verified
    against app/models/ops.py::TaskTracking — the brief's ⚠️ note flagged
    this needed confirming against real code)."""

    async def fake_fetch_one(sql, params=None):
        return {
            "dbos_workflow_id": "wf-123"
        }  # 已有 queued/in_progress 的 storage_audit

    monkeypatch.setattr(sr.db_engine, "fetch_one", fake_fetch_one)
    out = await sr._find_running_audit()
    assert out == "wf-123"


def test_find_running_audit_sql_caps_by_time(monkeypatch):
    """M4: a queued/in_progress storage_audit stuck forever (crashed worker)
    must not dedup every future /verify dispatch indefinitely — the query
    must bound itself to a recent window so a stale run self-heals."""
    captured = {}

    async def fake_fetch_one(sql, params=None):
        captured["sql"] = " ".join(sql.split())
        return None

    monkeypatch.setattr(sr.db_engine, "fetch_one", fake_fetch_one)
    import asyncio

    asyncio.run(sr._find_running_audit())
    assert "interval '2 hours'" in captured["sql"]
    assert "created_at >" in captured["sql"]


def test_fs_residue_where_covers_nine_columns():
    """I4: fs_residue must also catch the two extra parsed_media columns
    storage_migration's pm_assets module migrated to S3 (music_download_path,
    extract_audio_path) — previously only cover_download_path was covered,
    so a leftover filesystem path in either column would silently never
    surface as fs_residue."""
    assert sr._FS_RESIDUE_WHERE.count(" OR ") == 8  # 9 conditions, 8 joins
    for col in (
        "pm.download_path",
        "pm.cover_download_path",
        "pm.music_download_path",
        "pm.extract_audio_path",
        "r.thumbnail_path",
        "r.cover_image_path",
        "r.file_path",
        "rv.hls_path",
        "rv.file_path",
    ):
        assert col in sr._FS_RESIDUE_WHERE, col


@pytest.mark.asyncio
async def test_dispatch_deep_verify_precreates_task_tracking_before_dispatch(
    monkeypatch,
):
    """I5 fix: start_workflow_routed does NOT create any task_tracking row
    itself (verified against app/services/infra/dbos_orchestrator.py — it
    only calls DBOS.start_workflow/DBOSClient.enqueue and returns the
    workflow id; the row is created from INSIDE storage_audit_workflow's own
    body, which runs concurrently). Without a pre-create, the endpoint could
    return before that INSERT lands, so an immediate GET /audit poll sees no
    queued/in_progress row and the frontend's poll loop never starts.

    This test asserts the endpoint itself calls manager.create() (via
    get_task_manager()) BEFORE dispatching, and passes that SAME minted id
    through to start_workflow_routed's `workflow_id=` kwarg — so whichever
    id DBOS actually uses is the one already sitting in task_tracking."""

    async def fake_no_running(*a, **kw):
        return None

    monkeypatch.setattr(sr, "_find_running_audit", fake_no_running)

    fake_manager = MagicMock()
    fake_manager.create = AsyncMock()
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager",
        lambda: fake_manager,
    )

    dispatch = AsyncMock(
        return_value={
            "mode": "dbos",
            "task_type": "storage_audit",
            "dbos_workflow_id": "wf-precreated",
        }
    )
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )
    audit = AsyncMock(return_value=None)
    monkeypatch.setattr(sr, "create_audit_log", audit)

    result = await sr.dispatch_deep_verify(_auth(), _request())

    # create() happened, dispatch happened, and the id create() used is
    # exactly the id passed to start_workflow_routed(workflow_id=...).
    fake_manager.create.assert_awaited_once()
    created_id = fake_manager.create.call_args.kwargs["dbos_workflow_id"]
    dispatch.assert_awaited_once()
    assert dispatch.call_args.kwargs["workflow_id"] == created_id
    assert fake_manager.create.call_args.kwargs["task_type"] == "storage_audit"
    assert result == {"workflow_id": "wf-precreated", "already_running": False}

    # M3: audit log fired with the dispatched workflow id.
    audit.assert_awaited_once()
    audit_kwargs = audit.call_args.kwargs
    assert audit_kwargs["admin_id"] == "admin-1"
    assert audit_kwargs["action"] == "storage_audit_dispatch"
    assert audit_kwargs["target_id"] == "wf-precreated"
    assert audit_kwargs["ip_address"] == "10.0.0.9"


@pytest.mark.asyncio
async def test_dispatch_deep_verify_skips_precreate_when_already_running(
    monkeypatch,
):
    """When a scan is already queued/in_progress, dispatch must short-circuit
    before creating a second task_tracking row or dispatching a second
    workflow."""

    async def fake_running(*a, **kw):
        return "wf-already"

    monkeypatch.setattr(sr, "_find_running_audit", fake_running)

    fake_manager = MagicMock()
    fake_manager.create = AsyncMock()
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager",
        lambda: fake_manager,
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )
    audit = AsyncMock(return_value=None)
    monkeypatch.setattr(sr, "create_audit_log", audit)

    result = await sr.dispatch_deep_verify(_auth(), _request())

    assert result == {"workflow_id": "wf-already", "already_running": True}
    fake_manager.create.assert_not_awaited()
    dispatch.assert_not_awaited()
    audit.assert_not_awaited()
