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

import pytest

sr = importlib.import_module("app.api.admin.storage_router")


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
