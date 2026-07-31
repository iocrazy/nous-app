# Admin Media 页存储增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** admin Media 页集成存储可观测性——详情面板 Storage 段(各资产 sb:// key/单条 S3 核验) + 页头存储健康统计卡 + 存储状态筛选 + 全库深度 S3 扫描。

**Architecture:** 只读。统计/行状态/详情走实时 DB 查询;全库深扫走按钮触发的 DBOS workflow,结果存 task metadata(不建新表);详情另有单视频同步核验(≤6 个 HEAD)。后端一个新 router + 一个新 workflow;前端全部塞进现有 media 页。

**Tech Stack:** FastAPI + DBOS + asyncpg(后端);React + react-query + arco Design(admin 前端,无测试基建→tsc+build 验证)。

**Spec:** `docs/superpowers/specs/2026-07-31-admin-storage-management-page-design.md`(含 approved mockup 链接)

## Global Constraints

- bucket=`library`;sb:// 形态:内容寻址 `sb://library/t{scope}/{sha[:2]}/{sha[2:4]}/{sha}{ext}`、派生 `sb://library/derived/{rid}/{filename}`、HLS `sb://library/hls/{rid}/{vid}/master.m3u8`。
- 索引列(7 个):`parsed_media.download_path`/`cover_download_path`、`resources.thumbnail_path`/`cover_image_path`/`file_path`、`resource_versions.hls_path`/`file_path`。
- **只读**,零写操作;不建新表;扫描结果进 task metadata jsonb。
- 权限:全端点 `AdminAuthDep`(`app.core.admin_deps`)。
- 路线 C 纪律:workflow 用 `get_task_manager()` 的 create/start/complete + `patch_metadata`;失败 `raise` 不 return failed dict;**读任务状态只查 `task_tracking`,绝不查 `dbos.workflow_status`**。
- 存储状态四态:`ok`(绿)/`broken`(红,仅来自扫描)/`no_video`(灰,download_path NULL 或 status failed)/`fs_residue`(橙)。
- UI 文案英文 Title Case;admin 前端**无自动部署**,验证后手动 `cd deploy/gpu-server && NOUS_ANON_KEY=<key> docker compose up -d --build admin`。
- 分支:`feat/admin-storage-page`(已建,spec 已提交)。后端测试:`cd backend && uv run pytest tests/<file> -q`;lint:`uv run black <files> && uv run flake8 <files>`(black --check 必过,CI 用 black 不是 ruff)。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `backend/app/api/admin/storage_router.py` | Create | 6 端点:stats / media-status / media/{id}/detail / media/{id}/verify / verify(全库) / audit。数据逻辑抽成模块级 `_fetch_*`/`_verify_keys` 函数便于测试 |
| `backend/app/api/admin/__init__.py` | Modify | 挂载 storage_router(prefix="/storage") |
| `backend/app/workflows/storage_audit.py` | Create | `collect_audit_keys_step` + `storage_audit_workflow`(分块 exists 探测→patch_metadata) |
| `backend/tests/test_admin_storage_router.py` | Create | stats/media-status/detail/单核验 逻辑测试 |
| `backend/tests/test_storage_audit_workflow.py` | Create | collect UNION + 探测 missing/errors/截断 |
| `admin/src/api/endpoints/storage.ts` | Create | 类型 + react-query hooks(镜像 videos.ts) |
| `admin/src/pages/media/StorageSection.tsx` | Create | 详情弹窗 Storage 段(资产行 + Verify on S3) |
| `admin/src/pages/media/index.tsx` | Modify | 统计卡+4、工具栏 chips+Deep Verify、表格+2 列、详情挂 StorageSection |

---

### Task 1: 后端读端点(stats / media-status / detail)

**Files:**
- Create: `backend/app/api/admin/storage_router.py`
- Modify: `backend/app/api/admin/__init__.py`(import + include_router)
- Test: `backend/tests/test_admin_storage_router.py`

**Interfaces:**
- Consumes: `app.db.engine`(`fetch_all`/`fetch_one`,与 storage_migration.py 同款 `from app.db import engine as db_engine`)、`AdminAuthDep`。
- Produces(后续任务依赖):模块级 `router`(APIRouter);`_fetch_stats() -> dict`、`_fetch_media_status(media_ids: list[int]) -> list[dict]`、`_fetch_media_detail(media_id: int) -> dict`;Task 3 会在同文件追加 verify/audit 端点。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_admin_storage_router.py
"""Admin storage 只读端点的数据逻辑测试(monkeypatch db_engine,不起 HTTP)。"""
import pytest


@pytest.mark.asyncio
async def test_fetch_stats_shapes(monkeypatch):
    from app.api.admin import storage_router as sr

    async def fake_fetch_one(sql, params=None):
        s = " ".join(sql.split()).lower()
        if "sum(storage_size)" in s:
            return {"count": 1079, "size_bytes": 36507222016}
        if "hls_path" in s:
            return {"n": 108}
        if "orphan" in s or "r.id is null" in s:
            return {"n": 0}
        return {"n": 0}  # fs_residue

    async def fake_latest_audit():
        return {
            "status": "completed",
            "scanned": 3187, "errors": 0, "scanned_at": "2026-07-31T04:00:00Z",
            "missing": [{"key": "derived/9/x.webp", "kind": "thumbnail",
                         "media_id": None, "resource_id": "9"}],
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
    from app.api.admin import storage_router as sr

    rows = [
        # ok: 视频 sb://,cover/thumb/hls 齐
        {"media_id": 1, "video_key": "sb://library/t5/aa/bb/x.mp4", "video_size": 10,
         "video_download_status": "completed", "cover_path": "sb://library/derived/1/c.jpg",
         "thumbnail_path": "sb://library/derived/1/t.webp",
         "hls_path": "sb://library/hls/1/2/master.m3u8",
         "res_file_path": "sb://library/t5/aa/bb/x.mp4", "res_cover_image": None,
         "rv_file_path": "sb://library/t5/aa/bb/x.mp4", "scope_id": 5},
        # no_video: download_path NULL
        {"media_id": 2, "video_key": None, "video_size": None,
         "video_download_status": "failed", "cover_path": None, "thumbnail_path": None,
         "hls_path": None, "res_file_path": None, "res_cover_image": None,
         "rv_file_path": None, "scope_id": None},
        # fs_residue: 任一列非 sb:// 的路径
        {"media_id": 3, "video_key": "global/resources/web/x/video.mp4", "video_size": 5,
         "video_download_status": "completed", "cover_path": None, "thumbnail_path": None,
         "hls_path": None, "res_file_path": None, "res_cover_image": None,
         "rv_file_path": None, "scope_id": None},
    ]

    async def fake_fetch_all(sql, params=None):
        return rows

    monkeypatch.setattr(sr.db_engine, "fetch_all", fake_fetch_all)
    out = await sr._fetch_media_status([1, 2, 3])
    by = {r["media_id"]: r for r in out}
    assert by["1"]["storage_status"] == "ok" and by["1"]["cover_ok"] and by["1"]["hls_ok"]
    assert by["2"]["storage_status"] == "no_video"
    assert by["3"]["storage_status"] == "fs_residue"


@pytest.mark.asyncio
async def test_media_detail_assets(monkeypatch):
    from app.api.admin import storage_router as sr

    async def fake_fetch_one(sql, params=None):
        return {
            "media_id": 7, "download_path": "sb://library/t5/aa/bb/v.mp4",
            "storage_size": 197_000_000, "cover_download_path": "sb://library/derived/70/cover.jpg",
            "resource_id": 70, "thumbnail_path": "sb://library/derived/70/thumbnail.webp",
            "hls_path": "sb://library/hls/70/71/master.m3u8", "scope_id": 5,
        }

    monkeypatch.setattr(sr.db_engine, "fetch_one", fake_fetch_one)
    out = await sr._fetch_media_detail(7)
    kinds = {a["kind"]: a for a in out["assets"]}
    assert kinds["video"]["key"].endswith("v.mp4") and kinds["video"]["size_bytes"] == 197_000_000
    assert kinds["sprite"]["key"] == "sb://library/derived/70/preview_sprite.jpg"
    assert kinds["sprite"]["present_in_db"] is False
    assert kinds["hls"]["key"].endswith("master.m3u8")
    assert out["scope_id"] == "5"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_admin_storage_router.py -q`
Expected: FAIL(module `storage_router` 不存在)

- [ ] **Step 3: 实现 storage_router.py(读端点部分)**

```python
# backend/app/api/admin/storage_router.py
"""Admin storage observability — read-only endpoints over the S3-migrated
library. DB is the index (7 sb:// columns); this router exposes stats, per-row
storage status for the media table, and per-media asset detail. Deep S3
verification lives in Task 3 (same file). Read logic is module-level
``_fetch_*`` functions so tests hit them without HTTP/auth plumbing."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.core.admin_deps import AdminAuthDep
from app.db import engine as db_engine

router = APIRouter()

_FS = "LIKE '%/%' AND {c} NOT LIKE 'sb://%'"


def _fs_cond(col: str) -> str:
    return f"({col} IS NOT NULL AND {col} LIKE '%/%' AND {col} NOT LIKE 'sb://%')"


_FS_RESIDUE_WHERE = " OR ".join(
    [
        _fs_cond("pm.download_path"),
        _fs_cond("pm.cover_download_path"),
        _fs_cond("r.thumbnail_path"),
        _fs_cond("r.cover_image_path"),
        _fs_cond("r.file_path"),
        _fs_cond("rv.hls_path"),
        _fs_cond("rv.file_path"),
    ]
)

_JOINS = """
    FROM parsed_media pm
    LEFT JOIN resources r ON r.media_id = pm.id
    LEFT JOIN LATERAL (
        SELECT hls_path, file_path FROM resource_versions
        WHERE resource_id = r.id ORDER BY id DESC LIMIT 1
    ) rv ON true
"""


async def _fetch_latest_audit() -> dict:
    """Most recent storage_audit run, read from task_tracking (route C: never
    query dbos.workflow_status). Returns the audit dict the /audit endpoint
    serves; status 'none' when no scan has ever run."""
    row = await db_engine.fetch_one(
        """
        SELECT phase, metadata, completed_at FROM task_tracking
        WHERE task_type = 'storage_audit'
        ORDER BY created_at DESC LIMIT 1
        """
    )
    if not row:
        return {"status": "none", "scanned": 0, "errors": 0,
                "scanned_at": None, "missing": []}
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        import json

        meta = json.loads(meta or "{}")
    return {
        "status": row.get("phase") or "queued",
        "scanned": int(meta.get("scanned") or 0),
        "errors": int(meta.get("errors") or 0),
        "scanned_at": meta.get("scanned_at"),
        "missing": meta.get("missing") or [],
        "missing_truncated": bool(meta.get("missing_truncated")),
    }


async def _fetch_stats() -> dict:
    videos = await db_engine.fetch_one(
        """
        SELECT count(*) AS count, COALESCE(sum(storage_size),0)::bigint AS size_bytes
        FROM parsed_media WHERE download_path LIKE 'sb://%'
        """
    )
    fs = await db_engine.fetch_one(
        f"SELECT count(*) AS n {_JOINS} WHERE {_FS_RESIDUE_WHERE}"
    )
    orphans = await db_engine.fetch_one(
        """
        SELECT count(*) AS n FROM parsed_media pm
        LEFT JOIN resources r ON r.media_id = pm.id WHERE r.id IS NULL
        """
    )
    hls = await db_engine.fetch_one(
        "SELECT count(*) AS n FROM resource_versions WHERE hls_path LIKE 'sb://%'"
    )
    audit = await _fetch_latest_audit()
    return {
        "videos": {"count": int(videos["count"]), "size_bytes": int(videos["size_bytes"])},
        "fs_residue": int(fs["n"]),
        "orphans": int(orphans["n"]),
        "hls_ready": int(hls["n"]),
        "broken": len(audit["missing"]) if audit["status"] == "completed" else None,
        "last_scan": (
            {"at": audit["scanned_at"], "scanned": audit["scanned"],
             "missing": len(audit["missing"]), "errors": audit["errors"]}
            if audit["status"] == "completed" else None
        ),
    }


def _is_sb(v) -> bool:
    return bool(v) and str(v).startswith("sb://")


def _is_fs(v) -> bool:
    return bool(v) and "/" in str(v) and not str(v).startswith("sb://")


async def _fetch_media_status(media_ids: list[int]) -> list[dict]:
    rows = await db_engine.fetch_all(
        f"""
        SELECT pm.id AS media_id, pm.download_path AS video_key,
               pm.storage_size AS video_size, pm.video_download_status,
               pm.cover_download_path AS cover_path,
               r.thumbnail_path, r.cover_image_path AS res_cover_image,
               r.file_path AS res_file_path,
               rv.hls_path, rv.file_path AS rv_file_path, ri.scope_id
        {_JOINS}
        LEFT JOIN LATERAL (
            SELECT scope_id FROM resource_items
            WHERE resource_id = r.id ORDER BY id LIMIT 1
        ) ri ON true
        WHERE pm.id = ANY(:ids)
        """,
        {"ids": media_ids},
    )
    out = []
    for row in rows:
        fs = any(
            _is_fs(row.get(c))
            for c in ("video_key", "cover_path", "thumbnail_path",
                      "res_cover_image", "res_file_path", "hls_path", "rv_file_path")
        )
        if fs:
            st = "fs_residue"
        elif not row.get("video_key") or row.get("video_download_status") == "failed":
            st = "no_video"
        else:
            st = "ok"
        out.append(
            {
                "media_id": str(row["media_id"]),
                "video_key": row.get("video_key"),
                "video_size": row.get("video_size"),
                "cover_ok": _is_sb(row.get("cover_path")),
                "thumbnail_ok": _is_sb(row.get("thumbnail_path")),
                "hls_ok": _is_sb(row.get("hls_path")),
                "storage_status": st,
                "scope_id": str(row["scope_id"]) if row.get("scope_id") is not None else None,
            }
        )
    return out


async def _fetch_media_detail(media_id: int) -> dict:
    row = await db_engine.fetch_one(
        f"""
        SELECT pm.id AS media_id, pm.download_path, pm.storage_size,
               pm.cover_download_path, r.id AS resource_id, r.thumbnail_path,
               rv.hls_path,
               (SELECT scope_id FROM resource_items
                WHERE resource_id = r.id ORDER BY id LIMIT 1) AS scope_id
        {_JOINS}
        WHERE pm.id = :mid
        """,
        {"mid": media_id},
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    rid = row.get("resource_id")
    sprite_key = f"sb://library/derived/{rid}/preview_sprite.jpg" if rid else None
    assets = [
        {"kind": "video", "key": row.get("download_path"),
         "size_bytes": row.get("storage_size"), "present_in_db": _is_sb(row.get("download_path"))},
        {"kind": "cover", "key": row.get("cover_download_path"),
         "size_bytes": None, "present_in_db": _is_sb(row.get("cover_download_path"))},
        {"kind": "thumbnail", "key": row.get("thumbnail_path"),
         "size_bytes": None, "present_in_db": _is_sb(row.get("thumbnail_path"))},
        # sprite has no DB column anywhere — key is the serve-side convention
        {"kind": "sprite", "key": sprite_key, "size_bytes": None, "present_in_db": False},
        {"kind": "hls", "key": row.get("hls_path"),
         "size_bytes": None, "present_in_db": _is_sb(row.get("hls_path"))},
    ]
    return {
        "assets": assets,
        "scope_id": str(row["scope_id"]) if row.get("scope_id") is not None else None,
    }


@router.get("/stats")
async def get_storage_stats(auth: AdminAuthDep):
    return await _fetch_stats()


@router.get("/media-status")
async def get_media_status(auth: AdminAuthDep, media_ids: str):
    try:
        ids = [int(x) for x in media_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="media_ids must be comma-separated ints")
    if not ids or len(ids) > 200:
        raise HTTPException(status_code=422, detail="media_ids must contain 1-200 ids")
    return {"rows": await _fetch_media_status(ids)}


@router.get("/media/{media_id}/detail")
async def get_media_storage_detail(auth: AdminAuthDep, media_id: int):
    return await _fetch_media_detail(media_id)
```

- [ ] **Step 4: 挂载路由**

`backend/app/api/admin/__init__.py`:仿现有模式加两行——
```python
from .storage_router import router as storage_router
# …在 include_router 区(storage_migration_router 旁)加:
admin_router.include_router(
    storage_router, prefix="/storage", tags=["Admin - Storage"]
)
```

- [ ] **Step 5: 跑测试通过 + lint**

Run: `cd backend && uv run pytest tests/test_admin_storage_router.py -q`
Expected: 3 passed
Run: `uv run black app/api/admin/storage_router.py tests/test_admin_storage_router.py && uv run flake8 app/api/admin/storage_router.py tests/test_admin_storage_router.py`

⚠️ 若 db_engine.fetch_all 的 `ANY(:ids)` 绑定不被现有 engine 支持(看 storage_migration.py 现有用法核实),改为 `pm.id IN :ids`+`expanding` 或拼接 `unnest`;以能通过为准并保持参数化(禁止 f-string 拼 id)。

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/admin/storage_router.py backend/app/api/admin/__init__.py backend/tests/test_admin_storage_router.py
git commit -m "feat(admin): storage 只读端点 — stats/media-status/media detail"
```

---

### Task 2: storage_audit workflow(全库 S3 存在性扫描)

**Files:**
- Create: `backend/app/workflows/storage_audit.py`
- Test: `backend/tests/test_storage_audit_workflow.py`

**Interfaces:**
- Consumes: `app.services.library.media_storage.library_store()`(`.exists(key)`)、`app.db.engine`、`get_task_manager()`(`app.services.infra.unified_task_manager`,用法照 `storage_migration.py:1436-1553`)、`DBOS`。
- Produces: `storage_audit_workflow()`(无参 `@DBOS.workflow`,Task 3 派发)、`collect_audit_keys_step() -> list[dict]`(rows: `{"key","kind","media_id","resource_id"}`,key 已去 `sb://library/` 前缀)。metadata 契约:`{"kind":"storage_audit","scanned":int,"errors":int,"missing":[{key,kind,media_id,resource_id}],"missing_truncated":bool,"scanned_at":ISO}`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_storage_audit_workflow.py
"""storage_audit:key 收集(UNION 去重只收 sb://)与探测(missing/errors/截断)。"""
import pytest


@pytest.mark.asyncio
async def test_collect_keys_union_dedup(monkeypatch):
    from app.workflows import storage_audit as sa

    async def fake_fetch_all(sql, params=None):
        return [
            {"key": "sb://library/t5/aa/bb/v.mp4", "kind": "video",
             "media_id": 1, "resource_id": None},
            {"key": "sb://library/t5/aa/bb/v.mp4", "kind": "version_file",
             "media_id": None, "resource_id": 9},  # 同 key 不同来源 → 去重保留一条
            {"key": "sb://library/derived/9/t.webp", "kind": "thumbnail",
             "media_id": None, "resource_id": 9},
        ]

    monkeypatch.setattr(sa.db_engine, "fetch_all", fake_fetch_all)
    rows = await sa.collect_audit_keys_step()
    keys = [r["key"] for r in rows]
    assert keys == ["t5/aa/bb/v.mp4", "derived/9/t.webp"]  # 去前缀 + 去重


@pytest.mark.asyncio
async def test_probe_missing_and_errors(monkeypatch):
    from app.workflows import storage_audit as sa

    class FakeStore:
        async def exists(self, key):
            if key == "gone.jpg":
                return False
            if key == "boom.jpg":
                raise RuntimeError("storage down")
            return True

    rows = [
        {"key": "ok.mp4", "kind": "video", "media_id": 1, "resource_id": None},
        {"key": "gone.jpg", "kind": "cover", "media_id": 2, "resource_id": None},
        {"key": "boom.jpg", "kind": "thumbnail", "media_id": None, "resource_id": 3},
    ]
    missing, errors = await sa._probe_keys(FakeStore(), rows, chunk_size=2)
    assert [m["key"] for m in missing] == ["gone.jpg"]
    assert errors == 1  # 异常按不确定计 errors,不进 missing


def test_missing_truncation():
    from app.workflows.storage_audit import _cap_missing

    missing = [{"key": f"k{i}", "kind": "video",
                "media_id": i, "resource_id": None} for i in range(600)]
    capped, truncated = _cap_missing(missing)
    assert len(capped) == 500 and truncated is True
    capped2, truncated2 = _cap_missing(missing[:10])
    assert len(capped2) == 10 and truncated2 is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_storage_audit_workflow.py -q`
Expected: FAIL(module 不存在)

- [ ] **Step 3: 实现 storage_audit.py**

```python
# backend/app/workflows/storage_audit.py
"""Deep S3 existence audit for the migrated library.

Collects every sb:// key from the 7 index columns and probes each with
``store.exists`` in bounded chunks. Results go into the run's task_tracking
metadata (jsonb) — no new table. Route C: phase columns are trigger-owned;
this workflow only ``patch_metadata``s business fields and raises on failure.
A storage-call exception counts as *uncertain* (``errors``), never as
missing — network flaps must not masquerade as broken objects.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from dbos import DBOS
from loguru import logger

from app.db import engine as db_engine

_PREFIX = "sb://library/"
_CHUNK = 50
_MISSING_CAP = 500

_COLLECT_SQL = """
    SELECT download_path AS key, 'video' AS kind, id AS media_id,
           NULL::bigint AS resource_id
    FROM parsed_media WHERE download_path LIKE 'sb://%'
    UNION ALL
    SELECT cover_download_path, 'cover', id, NULL::bigint
    FROM parsed_media WHERE cover_download_path LIKE 'sb://%'
    UNION ALL
    SELECT thumbnail_path, 'thumbnail', NULL::bigint, id
    FROM resources WHERE thumbnail_path LIKE 'sb://%'
    UNION ALL
    SELECT cover_image_path, 'cover_image', NULL::bigint, id
    FROM resources WHERE cover_image_path LIKE 'sb://%'
    UNION ALL
    SELECT file_path, 'file', NULL::bigint, id
    FROM resources WHERE file_path LIKE 'sb://%'
    UNION ALL
    SELECT hls_path, 'hls', NULL::bigint, resource_id
    FROM resource_versions WHERE hls_path LIKE 'sb://%'
    UNION ALL
    SELECT file_path, 'version_file', NULL::bigint, resource_id
    FROM resource_versions WHERE file_path LIKE 'sb://%'
"""


async def collect_audit_keys_step() -> list[dict]:
    rows = await db_engine.fetch_all(_COLLECT_SQL)
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        key = str(r["key"])
        if key.startswith(_PREFIX):
            key = key[len(_PREFIX):]
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "key": key,
                "kind": r["kind"],
                "media_id": r.get("media_id"),
                "resource_id": r.get("resource_id"),
            }
        )
    return out


async def _probe_keys(store, rows: list[dict], chunk_size: int = _CHUNK):
    missing: list[dict] = []
    errors = 0

    async def probe(row):
        nonlocal errors
        try:
            if not await store.exists(row["key"]):
                missing.append(row)
        except Exception as e:  # noqa: BLE001 — uncertain, not missing
            errors += 1
            logger.debug(f"[storage-audit] exists() failed for {row['key']}: {e!r}")

    for i in range(0, len(rows), chunk_size):
        await asyncio.gather(*(probe(r) for r in rows[i : i + chunk_size]))
    return missing, errors


def _cap_missing(missing: list[dict]):
    if len(missing) > _MISSING_CAP:
        return missing[:_MISSING_CAP], True
    return missing, False


@DBOS.workflow()
async def storage_audit_workflow() -> dict[str, Any]:
    from app.services.infra.unified_task_manager import get_task_manager
    from app.services.library.media_storage import library_store

    manager = get_task_manager()
    task_id = DBOS.workflow_id
    await manager.create(
        task_id=task_id,
        task_type="storage_audit",
        title="Deep S3 storage audit",
    )
    await manager.start(task_id)

    rows = await collect_audit_keys_step()
    missing, errors = await _probe_keys(library_store(), rows)
    capped, truncated = _cap_missing(
        [
            {"key": m["key"], "kind": m["kind"],
             "media_id": str(m["media_id"]) if m.get("media_id") else None,
             "resource_id": str(m["resource_id"]) if m.get("resource_id") else None}
            for m in missing
        ]
    )
    result = {
        "kind": "storage_audit",
        "scanned": len(rows),
        "errors": errors,
        "missing": capped,
        "missing_truncated": truncated,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    await manager.patch_metadata(task_id, result)
    await manager.complete(task_id)
    logger.info(
        f"[storage-audit] scanned={len(rows)} missing={len(missing)} errors={errors}"
    )
    return {"scanned": len(rows), "missing": len(missing), "errors": errors}
```

⚠️ `manager.create/start/complete` 的实参签名以 `unified_task_manager.py`(create 在 :310)与 `storage_migration.py:1447-1553` 现场用法为准——若 create 需要额外必填字段(如 user_id/title 位置参数),照 storage_migration 的调用形状改,**不改语义**。

- [ ] **Step 4: 跑测试通过 + lint**

Run: `cd backend && uv run pytest tests/test_storage_audit_workflow.py -q`
Expected: 3 passed
Run: `uv run black app/workflows/storage_audit.py tests/test_storage_audit_workflow.py && uv run flake8 app/workflows/storage_audit.py tests/test_storage_audit_workflow.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/storage_audit.py backend/tests/test_storage_audit_workflow.py
git commit -m "feat(workflows): storage_audit — 全库 sb:// key 收集 + 分块 exists 探测存 task metadata"
```

---

### Task 3: verify 端点(单视频同步核验 + 全库派发 + audit 读取)

**Files:**
- Modify: `backend/app/api/admin/storage_router.py`(追加 3 端点 + `_verify_keys`)
- Test: `backend/tests/test_admin_storage_router.py`(追加)

**Interfaces:**
- Consumes: Task 1 的 `_fetch_media_detail`/`_fetch_latest_audit`;Task 2 的 `storage_audit_workflow`;`start_workflow_routed`(`app.services.infra.dbos_orchestrator`,用法照 `storage_migration_router.py:80-93`);`library_store()`。
- Produces: `POST /media/{media_id}/verify`、`POST /verify`、`GET /audit`;`_verify_keys(store, assets) -> list[dict]`。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 backend/tests/test_admin_storage_router.py

@pytest.mark.asyncio
async def test_verify_keys_exists_missing_uncertain():
    from app.api.admin.storage_router import _verify_keys

    class FakeStore:
        async def exists(self, key):
            if key.endswith("gone.jpg"):
                return False
            if key.endswith("boom.webp"):
                raise RuntimeError("down")
            return True

    assets = [
        {"kind": "video", "key": "sb://library/t5/aa/v.mp4"},
        {"kind": "cover", "key": "sb://library/derived/1/gone.jpg"},
        {"kind": "thumbnail", "key": "sb://library/derived/1/boom.webp"},
        {"kind": "hls", "key": None},  # 无 key 跳过
    ]
    out = await _verify_keys(FakeStore(), assets)
    by = {r["kind"]: r for r in out}
    assert by["video"]["exists"] is True
    assert by["cover"]["exists"] is False
    assert by["thumbnail"]["exists"] is None  # 异常 → 不确定
    assert "hls" not in by


@pytest.mark.asyncio
async def test_deep_verify_dedups_running(monkeypatch):
    from app.api.admin import storage_router as sr

    async def fake_fetch_one(sql, params=None):
        return {"task_id": "wf-123"}  # 已有 queued/in_progress 的 storage_audit

    monkeypatch.setattr(sr.db_engine, "fetch_one", fake_fetch_one)
    out = await sr._find_running_audit()
    assert out == "wf-123"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_admin_storage_router.py -q`
Expected: 新增 2 个 FAIL(函数不存在),原 3 个仍 PASS

- [ ] **Step 3: 追加实现**

```python
# 追加到 backend/app/api/admin/storage_router.py

_SB_PREFIX = "sb://library/"


async def _verify_keys(store, assets: list[dict]) -> list[dict]:
    """Probe each asset's key with store.exists. exists=None means the storage
    call itself failed (uncertain) — never conflate with missing."""
    out = []
    for a in assets:
        key = a.get("key")
        if not key:
            continue
        bare = key[len(_SB_PREFIX):] if key.startswith(_SB_PREFIX) else key
        try:
            ok = await store.exists(bare)
        except Exception:  # noqa: BLE001
            ok = None
        out.append({"kind": a["kind"], "key": key, "exists": ok})
    return out


async def _find_running_audit() -> str | None:
    row = await db_engine.fetch_one(
        """
        SELECT task_id FROM task_tracking
        WHERE task_type = 'storage_audit' AND phase IN ('queued','in_progress')
        ORDER BY created_at DESC LIMIT 1
        """
    )
    return row["task_id"] if row else None


@router.post("/media/{media_id}/verify")
async def verify_media_on_s3(auth: AdminAuthDep, media_id: int):
    from app.services.library.media_storage import library_store

    detail = await _fetch_media_detail(media_id)
    return {"results": await _verify_keys(library_store(), detail["assets"])}


@router.post("/verify")
async def dispatch_deep_verify(auth: AdminAuthDep):
    running = await _find_running_audit()
    if running:
        return {"workflow_id": running, "already_running": True}

    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.workflows.storage_audit import storage_audit_workflow

    result = await start_workflow_routed(
        "storage_audit",
        dbos_workflow_callable=storage_audit_workflow,
        dbos_workflow_kwargs={},
    )
    return {"workflow_id": result["dbos_workflow_id"], "already_running": False}


@router.get("/audit")
async def get_storage_audit(auth: AdminAuthDep):
    return await _fetch_latest_audit()
```

⚠️ `task_tracking` 的 workflow id 列名以 Task 1 提到的列清单为准(有 `task_id` 与 `dbos_workflow_id` 两列)——用 `_fetch_latest_audit`/`_find_running_audit` 前先 `information_schema` 或看 storage_migration_router 后续读法确认哪列存 DBOS.workflow_id;两函数保持一致。

- [ ] **Step 4: 跑测试 + lint**

Run: `cd backend && uv run pytest tests/test_admin_storage_router.py tests/test_storage_audit_workflow.py -q`
Expected: 8 passed
Run: `uv run black app/api/admin/storage_router.py tests/test_admin_storage_router.py && uv run flake8 app/api/admin/storage_router.py tests/test_admin_storage_router.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/admin/storage_router.py backend/tests/test_admin_storage_router.py
git commit -m "feat(admin): storage verify — 单视频同步核验 + 全库深扫派发(防重复) + audit 读取"
```

---

### Task 4: 前端 API 层 + 统计卡 + 工具栏(chips / Deep Verify)

**Files:**
- Create: `admin/src/api/endpoints/storage.ts`
- Modify: `admin/src/pages/media/index.tsx`(StatsCards 区 + 工具栏)

**Interfaces:**
- Consumes: `apiClient`(`admin/src/api/client.ts`)、react-query(镜像 `videos.ts`)。后端路径前缀:`/api/v1/admin/storage`。
- Produces(Task 5 依赖): `useStorageStats()`、`useAudit()`、`useMediaStatus(ids: string[])`、`useMediaStorageDetail(id: number | null)`、`useVerifyMedia()`、`useDeepVerify()`;类型 `MediaStorageRow`、`StorageAsset`、`StorageStats`、`AuditResult`;`storageFilter` state 提升到 media 页(`'all'|'ok'|'broken'|'no_video'|'fs_residue'`)。

- [ ] **Step 1: 写 storage.ts**

```typescript
// admin/src/api/endpoints/storage.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

const BASE = '/api/v1/admin/storage'

export interface StorageStats {
  videos: { count: number; size_bytes: number }
  fs_residue: number
  orphans: number
  hls_ready: number
  broken: number | null
  last_scan: { at: string; scanned: number; missing: number; errors: number } | null
}

export interface MediaStorageRow {
  media_id: string
  video_key: string | null
  video_size: number | null
  cover_ok: boolean
  thumbnail_ok: boolean
  hls_ok: boolean
  storage_status: 'ok' | 'no_video' | 'fs_residue'
  scope_id: string | null
}

export interface StorageAsset {
  kind: 'video' | 'cover' | 'thumbnail' | 'sprite' | 'hls'
  key: string | null
  size_bytes: number | null
  present_in_db: boolean
}

export interface AuditMissing {
  key: string
  kind: string
  media_id: string | null
  resource_id: string | null
}

export interface AuditResult {
  status: 'none' | 'queued' | 'in_progress' | 'completed' | 'failed'
  scanned: number
  errors: number
  scanned_at: string | null
  missing: AuditMissing[]
  missing_truncated?: boolean
}

export function useStorageStats() {
  return useQuery({
    queryKey: ['storage-stats'],
    queryFn: async () => (await apiClient.get<StorageStats>(`${BASE}/stats`)).data,
  })
}

export function useAudit() {
  return useQuery({
    queryKey: ['storage-audit'],
    queryFn: async () => (await apiClient.get<AuditResult>(`${BASE}/audit`)).data,
    refetchInterval: (q) =>
      q.state.data?.status === 'queued' || q.state.data?.status === 'in_progress'
        ? 4000
        : false,
  })
}

export function useMediaStatus(ids: string[]) {
  return useQuery({
    queryKey: ['storage-media-status', ids.join(',')],
    enabled: ids.length > 0,
    queryFn: async () =>
      (
        await apiClient.get<{ rows: MediaStorageRow[] }>(`${BASE}/media-status`, {
          params: { media_ids: ids.join(',') },
        })
      ).data.rows,
  })
}

export function useMediaStorageDetail(id: number | null) {
  return useQuery({
    queryKey: ['storage-media-detail', id],
    enabled: id != null,
    queryFn: async () =>
      (
        await apiClient.get<{ assets: StorageAsset[]; scope_id: string | null }>(
          `${BASE}/media/${id}/detail`,
        )
      ).data,
  })
}

export function useVerifyMedia() {
  return useMutation({
    mutationFn: async (id: number) =>
      (
        await apiClient.post<{
          results: { kind: string; key: string; exists: boolean | null }[]
        }>(`${BASE}/media/${id}/verify`)
      ).data.results,
  })
}

export function useDeepVerify() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      (
        await apiClient.post<{ workflow_id: string; already_running: boolean }>(
          `${BASE}/verify`,
        )
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['storage-audit'] })
    },
  })
}
```

- [ ] **Step 2: media/index.tsx — StatsCards 扩展**

在现有 `StatsCards()`(index.tsx:82 起)追加 4 张卡。保持现有 Grid.Row/Card/Statistic 结构与列宽风格(现有 6 卡 span=4;改为两行或 span 调小,以现有布局风格为准):

```tsx
// StatsCards 内追加(useStorageStats/useAudit 从 ../../api/endpoints/storage 导入):
const { data: sst } = useStorageStats()
const { data: audit } = useAudit()
// …在现有卡后追加:
<Grid.Col span={4}><Card>
  <Statistic title="FS Residue" value={sst?.fs_residue ?? '—'}
    styleValue={{ color: (sst?.fs_residue ?? 0) > 0 ? '#b6740a' : '#178a4c' }} />
</Card></Grid.Col>
<Grid.Col span={4}><Card>
  <Statistic title="Orphans" value={sst?.orphans ?? '—'} />
</Card></Grid.Col>
<Grid.Col span={4}><Card>
  <Statistic title="Broken on S3"
    value={sst?.broken ?? '—'}
    styleValue={{ color: (sst?.broken ?? 0) > 0 ? '#cf3535' : '#178a4c' }} />
</Card></Grid.Col>
<Grid.Col span={4}><Card>
  <Statistic title="HLS Ready" value={sst?.hls_ready ?? '—'} />
</Card></Grid.Col>
```

统计卡行下方加一行 last-scan 小字(audit.status==='completed' 时):`Last S3 scan {formatDateTime(audit.scanned_at)} · {audit.scanned} objects · {audit.missing.length} missing`;status none 时显示 `No deep scan yet`。

- [ ] **Step 3: 工具栏 — 筛选 chips + Deep Verify 按钮**

在 media 页搜索区旁加(用 arco 现有组件风格,`Radio.Group type='button'` 或 Tag 组):

```tsx
const [storageFilter, setStorageFilter] = useState<'all'|'ok'|'broken'|'no_video'|'fs_residue'>('all')
const deepVerify = useDeepVerify()
const auditRunning = audit?.status === 'queued' || audit?.status === 'in_progress'
// chips:
<Radio.Group type="button" size="small" value={storageFilter}
  onChange={(v) => setStorageFilter(v)}
  options={[
    { label: 'All', value: 'all' }, { label: 'OK', value: 'ok' },
    { label: 'Broken', value: 'broken' }, { label: 'No Video', value: 'no_video' },
    { label: 'FS Residue', value: 'fs_residue' },
  ]} />
// 按钮:
<Button loading={deepVerify.isPending || auditRunning}
  onClick={() => deepVerify.mutate()}>
  {auditRunning ? 'Verifying…' : 'Deep Verify'}
</Button>
```

筛选逻辑在 Task 5 表格数据接好后生效(本任务先接 state,不筛也可编译)。

- [ ] **Step 4: 验证(无测试基建 → typecheck+build)**

Run: `cd admin && npx tsc --noEmit && npm run build`
Expected: 0 error,build 成功

- [ ] **Step 5: Commit**

```bash
git add admin/src/api/endpoints/storage.ts admin/src/pages/media/index.tsx
git commit -m "feat(admin-ui): storage API hooks + media 页统计卡/筛选 chips/Deep Verify"
```

---

### Task 5: 表格列 + 详情 Storage 段

**Files:**
- Create: `admin/src/pages/media/StorageSection.tsx`
- Modify: `admin/src/pages/media/index.tsx`(表格 2 列 + 筛选生效 + 详情弹窗挂载)

**Interfaces:**
- Consumes: Task 4 全部 hooks/类型;现有 `NotionColumnDef`、详情 Modal(`useVideoDetail` 渲染处)、`formatBytes`。
- Produces: 完整功能。

- [ ] **Step 1: StorageSection 组件**

```tsx
// admin/src/pages/media/StorageSection.tsx
import { useState } from 'react'
import { Button, Tag, Typography } from '@arco-design/web-react'
import { useMediaStorageDetail, useVerifyMedia } from '../../api/endpoints/storage'
import { formatBytes } from '../../utils/format'

const KIND_LABEL: Record<string, string> = {
  video: 'Video', cover: 'Cover', thumbnail: 'Thumbnail',
  sprite: 'Preview Sprite', hls: 'HLS',
}

export function StorageSection({ mediaId }: { mediaId: number }) {
  const { data } = useMediaStorageDetail(mediaId)
  const verify = useVerifyMedia()
  const [results, setResults] = useState<Record<string, boolean | null>>({})

  if (!data) return null

  const dot = (kind: string, presentInDb: boolean) => {
    const v = results[kind]
    if (v === true) return <Tag color="green" size="small">OK</Tag>
    if (v === false) return <Tag color="red" size="small">Missing</Tag>
    if (v === null && kind in results)
      return <Tag color="gray" size="small">Unknown</Tag>
    return presentInDb
      ? <Tag color="arcoblue" size="small">In DB</Tag>
      : <Tag color="gray" size="small">—</Tag>
  }

  return (
    <div style={{ marginTop: 16 }}>
      <Typography.Title heading={6} style={{ marginBottom: 8 }}>
        Storage
      </Typography.Title>
      {data.assets.map((a) => (
        <div key={a.kind}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0',
                   borderBottom: '1px dashed var(--color-border-2)' }}>
          <span style={{ width: 110, fontWeight: 600, fontSize: 12 }}>
            {KIND_LABEL[a.kind]}
          </span>
          <Typography.Text ellipsis={{ showTooltip: true }}
            style={{ flex: 1, fontFamily: 'monospace', fontSize: 11,
                     color: 'var(--color-text-3)' }}>
            {a.key ?? '—'}
          </Typography.Text>
          {a.size_bytes != null && (
            <span style={{ fontFamily: 'monospace', fontSize: 11 }}>
              {formatBytes(a.size_bytes)}
            </span>
          )}
          {dot(a.kind, a.present_in_db)}
        </div>
      ))}
      <Button size="small" style={{ marginTop: 10 }} loading={verify.isPending}
        onClick={() =>
          verify.mutate(mediaId, {
            onSuccess: (rs) => {
              const next: Record<string, boolean | null> = {}
              rs.forEach((r) => { next[r.kind] = r.exists })
              setResults(next)
            },
          })
        }>
        Verify on S3
      </Button>
    </div>
  )
}
```

- [ ] **Step 2: 表格加 Assets / Storage 两列 + 筛选生效**

在 media 页列表数据处:取当前页 `media_ids`(现有列表 rows 的 id),`const { data: storageRows } = useMediaStatus(ids)`;`const storageBy = useMemo(() => new Map((storageRows ?? []).map(r => [r.media_id, r])), [storageRows])`;`const brokenSet = useMemo(() => new Set((audit?.missing ?? []).map(m => m.media_id).filter(Boolean)), [audit])`。

列定义追加(仿现有 NotionColumnDef 形状):

```tsx
{
  key: 'assets', title: 'Assets', width: 110,
  render: (record: VideoData) => {
    const s = storageBy.get(String(record.id))
    if (!s) return null
    const cell = (label: string, ok: boolean) => (
      <span style={{
        display: 'inline-grid', placeItems: 'center', width: 18, height: 18,
        borderRadius: 4, marginRight: 3, fontSize: 10, fontWeight: 700,
        background: ok ? 'var(--color-success-light-1)' : 'var(--color-fill-2)',
        color: ok ? 'rgb(var(--success-6))' : 'var(--color-text-3)',
      }}>{label}</span>
    )
    return <>{cell('C', s.cover_ok)}{cell('T', s.thumbnail_ok)}{cell('H', s.hls_ok)}</>
  },
},
{
  key: 'storage', title: 'Storage', width: 120,
  render: (record: VideoData) => {
    const s = storageBy.get(String(record.id))
    if (!s) return null
    if (brokenSet.has(String(record.id)))
      return <Tag color="red" size="small">Broken</Tag>
    if (s.storage_status === 'ok') return <Tag color="green" size="small">OK</Tag>
    if (s.storage_status === 'fs_residue')
      return <Tag color="orange" size="small">FS Residue</Tag>
    return <Tag color="gray" size="small">No Video</Tag>
  },
},
```

筛选生效(对已加载行,前端过滤):

```tsx
const filteredRows = useMemo(() => {
  if (storageFilter === 'all') return rows
  return rows.filter((r) => {
    const s = storageBy.get(String(r.id))
    if (storageFilter === 'broken') return brokenSet.has(String(r.id))
    return s?.storage_status === storageFilter
  })
}, [rows, storageFilter, storageBy, brokenSet])
```

表格 data 换成 `filteredRows`。

- [ ] **Step 3: 详情弹窗挂 StorageSection**

在现有 VideoDetail 渲染处(Modal "Video Details" 内容底部)加:
```tsx
<StorageSection mediaId={detail.id} />
```
(import 自 `./StorageSection`;`detail.id` 用现有 VideoDetailData 的 id 字段,若字段名不同以文件内实际为准。)

- [ ] **Step 4: 验证**

Run: `cd admin && npx tsc --noEmit && npm run build`
Expected: 0 error,build 成功

手测清单(本地 `cd admin && npm run dev` → http://localhost:3097,后端指向生产或本地):
1. media 页统计卡出现 4 张新卡,数字合理(FS Residue=0 绿)
2. 表格出现 Assets(C/T/H)与 Storage 列
3. 点行开详情 → Storage 段列出各资产 key/大小 → 点 Verify on S3 → 状态 Tag 更新
4. 点 Deep Verify → 按钮转 loading → 完成后 last-scan 行 + Broken 卡刷新
5. chips 切 OK/No Video 表格行随之过滤

- [ ] **Step 5: Commit**

```bash
git add admin/src/pages/media/StorageSection.tsx admin/src/pages/media/index.tsx
git commit -m "feat(admin-ui): 表格 Assets/Storage 列 + 详情 Storage 段(单条 S3 核验)"
```

---

### Task 6: 端到端验证 + 部署备忘

**Files:** 无新文件(验证任务)

- [ ] **Step 1: 后端全量测试 + lint**

Run: `cd backend && uv run pytest tests/test_admin_storage_router.py tests/test_storage_audit_workflow.py -q && uv run black --check app/api/admin/storage_router.py app/workflows/storage_audit.py && uv run flake8 app/api/admin/storage_router.py app/workflows/storage_audit.py`
Expected: 全绿

- [ ] **Step 2: 真机冒烟(gpupc 生产容器,只读安全)**

后端合并部署后(或本地起 backend):
```bash
TOK=<admin JWT>
curl -sS -H "Authorization: Bearer $TOK" http://localhost:8080/api/v1/admin/storage/stats | head -c 400
curl -sS -H "Authorization: Bearer $TOK" "http://localhost:8080/api/v1/admin/storage/media-status?media_ids=<某真实id>"
curl -sS -X POST -H "Authorization: Bearer $TOK" http://localhost:8080/api/v1/admin/storage/media/<id>/verify
curl -sS -X POST -H "Authorization: Bearer $TOK" http://localhost:8080/api/v1/admin/storage/verify   # 派发深扫
curl -sS -H "Authorization: Bearer $TOK" http://localhost:8080/api/v1/admin/storage/audit             # 完成后 missing 应≈0
```
Expected: stats 数字与已知一致(videos≈1079/34GB、fs_residue=0);深扫完成后 audit.status=completed。

- [ ] **Step 3: admin 前端手动部署(合并后)**

```bash
cd deploy/gpu-server && NOUS_ANON_KEY=<key> docker compose up -d --build admin
```
验证 https://admin.nous.ink Media 页新元素可见。

---

## Self-Review

1. **Spec 覆盖**:6 端点(T1 三读 + T3 三 verify/audit)✓;storage_audit workflow(T2)✓;storage.ts(T4)✓;统计卡/chips/Deep Verify(T4)✓;表格 2 列/broken 叠加/前端筛选(T5)✓;详情 Storage 段+单条核验(T5)✓;不建新表/只读/metadata 截断/防重复派发/errors 不误报 ✓;admin 手动部署(T6)✓。
2. **占位扫描**:无 TBD;两处 ⚠️ 是"以现场代码为准"的核实指令(带确切参考位置),非占位。
3. **类型一致性**:`_fetch_media_detail` 返回 shape 与 T3 `_verify_keys` 入参、T4 `StorageAsset`、T5 组件消费一致;`media_id` 统一 str(前端)对 int(路径参数);`storage_status` 三态(broken 前端叠加)口径 T1/T4/T5 一致。
