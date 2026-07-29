# 存量全量迁 S3 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把仍在文件系统（CIFS）上的全部业务成品（787 源文件 29GB / 97 HLS 3万分片 / sprite / 缩略图 / 封面）迁到对象存储，并让新下载直接落 S3，消除「两套存储并存」这一持续出错源。

**Architecture:** 分四个依赖有序的 PR。先接线（新下载落 S3，止血）→ 再补齐迁移工具（三个新 module + 读取端前缀分发）→ 再执行存量迁移（dry-run → 复制 → 校验 → 后置删除，全程幂等可回退）→ 最后清理遗留（file_type 修正）。**成品进 S3，转码/缩略图的中间临时文件仍走本地盘**（`materialize()` 守护此边界）。

**Tech Stack:** Python 3.13 / FastAPI / DBOS · Supabase Storage（S3 协议，SeaweedFS on nas-B）· pytest(asyncio_mode=auto)

## Global Constraints

- 依据 spec：`docs/superpowers/specs/2026-07-28-storage-full-s3-migration-design.md`
- **指导思想（分层）**：成品进 S3；单机高频临时（转码中间产物、`materialize()` 拉的临时文件）走本地盘；CIFS 清空
- **迁移用「复制 + 校验 + 后置删除」，绝不用 move** —— 29GB 不可逆操作必须有回退窗口
- **顺序不可反：先接线下载链路，后迁存量** —— 否则迁移期间新下载继续落文件系统
- **migration 编号从 393 起** —— master 上 384 已被占用两次（`384_resources_gen_prompt_negative` + `384_fix_stale_date_bucket_version_paths`），385~392 也已用，下一个可用是 393
- 测试命令：`cd backend && uv run pytest`
- 提交前必跑 lint：`cd backend && uv run black <改的.py> && uv run isort <改的.py> && uv run flake8 <改的.py>`
- 提交信息结尾附：`Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA`
- 代码注释与提交信息用中文；UI 文案英文
- **验收纪律（CLAUDE.md）**：读正常 ≠ 服务正常，每个 PR 必须端到端写入冒烟，不能只测读
- 环境噪音：`pyiceberg` 的 Pydantic 弃用告警；`tests/startup/test_healthz_lite.py::test_stop_kills_server` 是沙箱网络 flake

## 前置（已确认，2026-07-28）

- PR #1601 已合并，92 个失效行已对齐到 `global/` 布局，0 个仍是旧日期桶
- `storage_migration.py` 现有两个 module：`uploads` / `project_files`，模板结构：`ModuleConfig(name, select_sql, extract, update_row)`
- `resolve_media_source(file_path)` 现在只分 `sb://`（对象存储）vs 其他（文件系统相对路径）
- `MediaLocation` 有 `backend / rel_path / bucket / key` 与 `is_object_store` 属性
- 迁移基线：`resource_versions` 待迁 787 行 / 29GB；HLS 待迁 97 个；sprite 778 个

---

## 文件结构

| 动作 | 路径 | 职责 |
|------|------|------|
| 修改 | `backend/app/services/media/downloader/downloader.py` | 下载落盘后追加「传 S3 + file_path 写 sb://」（PR-1） |
| 修改 | `backend/app/workflows/download.py` | `create_version` 的 file_path 用 S3 路径（PR-1） |
| 新建 | `supabase/migrations/393_resource_versions_storage_status.sql` | `storage_status` 列（PR-2） |
| 修改 | `backend/app/services/library/media_storage.py` | `MediaLocation.is_prefix` + `resolve_media_source` 前缀分发（PR-2） |
| 修改 | `backend/app/workflows/storage_migration.py` | 新增 `downloads` / `hls` / `derived` 三个 module（PR-3） |
| 修改 | `backend/app/api/resources_crud_router.py` | sprite 端点去分叉 + 修 NULL file_path 的 404 bug（PR-3） |
| —— | （执行，非代码） | dry-run → 复制 → 校验 → 后置删除（PR-4） |

---

# PR-1 `feat/download-to-s3`：新下载直接落 S3（止血）

**这一步先做，让文件系统不再增长新债务。** 存量一个都不动。

### Task 1：下载落盘后追加 S3 上传，file_path 写 sb://

**Files:**
- Modify: `backend/app/services/media/downloader/downloader.py`（`download_video_by_platform_id` 落盘后，约 :642 `download_path=video_relative_path` 附近）
- Modify: `backend/app/workflows/download.py:451`（`"file_path": fresh_download_path` → S3 路径）
- Test: `backend/tests/test_download_to_s3.py`

**Interfaces:**
- Consumes: `ObjectStore.put_file(key, path, mime)`、`MediaKeyBuilder.content_key`、`store_local_file`（`media_storage.py` 已有）、`unified_storage_enabled()`（`storage_flag.py` 已有的开关）
- Produces: 下载完成后 `resource_versions.file_path` 形如 `sb://library/{scope}/{sha[:2]}/{sha[2:4]}/{sha}.mp4`

- [ ] **Step 1：写失败测试 —— 开关开时，下载产物落 S3 且 file_path 是 sb://**

`backend/tests/test_download_to_s3.py`：

```python
"""下载链路接 S3 —— 开关开时新下载落对象存储,file_path 写 sb://。

这是「先接线后迁移」的第一步:止血,让文件系统不再增长。
"""

import hashlib
import os
import tempfile

import pytest

from app.services.library.media_storage import store_local_file, resolve_media_source


async def test_store_local_file_produces_sb_path(monkeypatch):
    """store_local_file 把本地文件传 S3 并返回 sb:// 形态的 StoredObject。"""
    payload = os.urandom(1024 * 512)
    fd, src = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    open(src, "wb").write(payload)

    from app.services.library.media_storage import library_store
    store = library_store()
    stored = await store_local_file(
        store, source_path=src, scope_id=1, mime="video/mp4", filename="v.mp4"
    )
    os.unlink(src)

    assert stored.file_path.startswith("sb://library/")
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    loc = resolve_media_source(stored.file_path)
    assert loc.is_object_store
    # 清理
    await store.remove(loc.key)
```

- [ ] **Step 2：跑测试确认失败/通过**

```bash
cd backend && uv run pytest tests/test_download_to_s3.py -v
```

`store_local_file` 已存在（`media_storage.py:484`），此测试验证既有能力可复用。若已通过，说明基建就绪，进 Step 3 接线；若失败，先补 `store_local_file` 的缺口。

- [ ] **Step 3：在下载落盘后接线**

Read `downloader.py` 的 `download_video_by_platform_id`（:478-660），在它算出本地 `video_relative_path`、文件已落盘之后，加一段（受开关控制）：

```python
        # 存储分层:新下载的成品直接进 S3(止血,不再增长文件系统债务)。
        # 开关关时保持旧行为(落文件系统),便于回退。
        from app.services.library.storage_flag import unified_storage_enabled

        if await unified_storage_enabled():
            from app.services.library.media_storage import library_store, store_local_file

            abs_path = os.path.join(settings.DOWNLOAD_PATH, video_relative_path)
            stored = await store_local_file(
                library_store(),
                source_path=abs_path,
                scope_id=scope_id,
                mime="video/mp4",
                filename=os.path.basename(video_relative_path),
            )
            # 成品已在 S3,file_path 改写 sb://;本地文件留给转码 materialize 后由
            # 迁移的 delete_source 阶段统一回收(不在此处删,避免转码链路取不到)。
            video_relative_path = stored.file_path
```

⚠️ **不要在这里删本地文件** —— 转码链路可能马上要读它，删了会断。本地文件的回收由 PR-4 的 `delete_source` 阶段统一做。

- [ ] **Step 4：download.py 的 create_version 用这个路径**

`download.py:451` 的 `"file_path": fresh_download_path` 保持不变 —— 因为 `fresh_download_path` 现在已经是 Step 3 改写后的 `sb://` 值（若开关开）。确认这条链路把 downloader 的返回值透传到了 `fresh_download_path`；若中间有变量断层，在此接上。

- [ ] **Step 5：端到端冒烟（真实下载 → 验证落 S3）**

```bash
# 开关需为开(生产已开 unified_storage,确认:)
docker exec nous-backend /app/.venv/bin/python -c \
  "import asyncio; from app.services.library.storage_flag import unified_storage_enabled; print(asyncio.run(unified_storage_enabled()))"
# 真实推一个下载,查落地路径(用测试账号,参考本会话已用的 /media/fetch 冒烟)
```

Expected：新下载的 `resource_versions.file_path` 是 `sb://library/...`，且对象存储里真有该对象。

- [ ] **Step 6：lint + 提交**

```bash
cd backend && uv run black app/services/media/downloader/downloader.py app/workflows/download.py tests/test_download_to_s3.py && uv run isort <同上> && uv run flake8 <同上>
git add -A backend
git commit -m "feat(download): 新下载直接落 S3（止血,存量不动）

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

# PR-2 `feat/album-prefix-dispatch`：图集前缀形态 + storage_status 列

**读取端先具备处理「前缀形态资源」的能力，再迁数据（决策 1 的顺序要求）。** 图集迁 S3 后一个资源对应多个对象，`file_path` 存前缀。

### Task 2：storage_status 列

**Files:**
- Create: `supabase/migrations/394_resource_versions_storage_status.sql`
  （原计划 393，但 origin/master 的 caption_classify fix 已占用 393，改用 394）

- [ ] **Step 1：写 migration**

```sql
-- resource_versions.storage_status: 迁移探测不到源文件时标记,不删行。
-- 95 个旧日期桶失效行(PR #1601 已对齐 87 个,剩 5 个 B站无 parsed_media
-- 记录)用这个标记,让迁移跳过、前端显示"文件已丢失"而非无限加载。
ALTER TABLE resource_versions
  ADD COLUMN IF NOT EXISTS storage_status text NOT NULL DEFAULT 'ok';
-- 取值: 'ok' | 'source_missing'
COMMENT ON COLUMN resource_versions.storage_status IS
  '迁移/读取时的源文件存在性: ok=正常, source_missing=文件已丢失(记录保留)';
```

- [ ] **Step 2：本地验证**

```bash
docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  "\d resource_versions" | grep storage_status
```

Expected：列存在，默认 `'ok'`。

- [ ] **Step 3：提交**

```bash
git add supabase/migrations/394_resource_versions_storage_status.sql
git commit -m "feat(db): resource_versions.storage_status 列(mig 394)

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

### Task 3：MediaLocation.is_prefix + resolve_media_source 前缀分发

**Files:**
- Modify: `backend/app/services/library/media_storage.py`（`MediaLocation` :45、`resolve_media_source` :60）
- Test: `backend/tests/test_media_location_prefix.py`

**Interfaces:**
- Produces: `MediaLocation.is_prefix`（bool，key 以 `/` 结尾即前缀形态）；`resolve_media_source("sb://library/.../album/{rid}/")` 返回 `is_prefix=True`

- [ ] **Step 1：写失败测试**

`backend/tests/test_media_location_prefix.py`：

```python
"""图集前缀形态:sb://.../album/{rid}/ 以 / 结尾表示「一个前缀下多个对象」。

单对象资源(视频)file_path 是 sb://library/ab/cd/sha.mp4;
图集资源迁 S3 后 file_path 是 sb://library/{scope}/album/{rid}/,读取端
按前缀 list_prefix 取全部 slides。
"""

from app.services.library.media_storage import resolve_media_source


def test_single_object_is_not_prefix():
    loc = resolve_media_source("sb://library/ab/cd/deadbeef.mp4")
    assert loc.is_object_store
    assert loc.is_prefix is False
    assert loc.key == "ab/cd/deadbeef.mp4"


def test_trailing_slash_is_prefix():
    loc = resolve_media_source("sb://library/t1/album/123/")
    assert loc.is_object_store
    assert loc.is_prefix is True
    assert loc.key == "t1/album/123/"


def test_filesystem_path_not_prefix():
    loc = resolve_media_source("global/resources/web/douyin/x/video.mp4")
    assert loc.is_object_store is False
    assert loc.is_prefix is False
```

- [ ] **Step 2：跑测试确认失败**

```bash
cd backend && uv run pytest tests/test_media_location_prefix.py -v
```

Expected：FAIL — `MediaLocation` 无 `is_prefix` 属性。

- [ ] **Step 3：加 is_prefix 属性**

`media_storage.py` 的 `MediaLocation` 类，在 `is_object_store` 旁加：

```python
    @property
    def is_prefix(self) -> bool:
        """key 以 / 结尾 = 前缀形态(一个资源对应该前缀下的多个对象,如图集)。"""
        return self.is_object_store and bool(self.key) and self.key.endswith("/")
```

`resolve_media_source` 无需改（它已经把 `sb://library/t1/album/123/` 的 key 解析成 `t1/album/123/`，尾斜杠自然保留）。确认 partition 逻辑不吃掉尾斜杠。

- [ ] **Step 4：跑测试确认通过**

```bash
cd backend && uv run pytest tests/test_media_location_prefix.py -v
```

Expected：3 passed。

- [ ] **Step 5：lint + 提交**

```bash
cd backend && uv run black app/services/library/media_storage.py tests/test_media_location_prefix.py && uv run isort <同上> && uv run flake8 <同上>
git add -A backend supabase
git commit -m "feat(storage): MediaLocation.is_prefix + storage_status,为图集迁移铺路

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

# PR-3 `feat/migration-modules`：三个迁移 module + sprite 去分叉

### Task 4：storage_migration 新增 downloads module（含图集展开）

**Files:**
- Modify: `backend/app/workflows/storage_migration.py`（`_MODULES` :270、新增 `_DOWNLOADS_SELECT_SQL` / `_downloads_extract` / `_downloads_update_row`）
- Test: `backend/tests/test_storage_migration_downloads.py`

**Interfaces:**
- Consumes: 现有 `ModuleConfig` / `RowExtract` / `_migrate_row`
- Produces: `_MODULES["downloads"]`，选取 `source_type='web'` 且 `file_path` 非 sb:// 的行

- [ ] **Step 1：写 SELECT SQL（这次故意 IN ('web')，与 uploads 相反）**

现有 `uploads` 的 SQL 用 `source_type IN ('upload','generated','derived')` **排除** web。新 module 反过来专收 web：

```python
_DOWNLOADS_SELECT_SQL = """
    SELECT rv.id, rv.resource_id, rv.version_number, rv.file_path,
           rv.filename, rv.mime_type, r.current_version, ri.scope_id,
           pm.id AS parsed_media_id
    FROM resource_versions rv
    JOIN resources r ON r.id = rv.resource_id
    LEFT JOIN LATERAL (
      SELECT scope_id FROM resource_items WHERE resource_id = r.id
      ORDER BY id LIMIT 1
    ) ri ON true
    LEFT JOIN LATERAL (
      SELECT id FROM parsed_media pm2
      WHERE rv.file_path LIKE '%'||pm2.id||'%' LIMIT 1
    ) pm ON true
    WHERE rv.file_path IS NOT NULL
      AND rv.file_path NOT LIKE 'sb://%'
      AND rv.storage_status = 'ok'
      AND r.source_type = 'web'
      AND (CAST(:scope_id AS bigint) IS NULL OR ri.scope_id = :scope_id)
    ORDER BY rv.id LIMIT :limit
"""
```

- [ ] **Step 2：写 extract —— 区分「视频文件」与「图集目录」**

图集的 `file_path` 指向目录（`global/resources/web/douyin/{pm_id}/`，内含 `{aweme}_0.jpg` + `cover.jpg`）。extract 要判断 isdir，图集展开为前缀形态：

```python
def _downloads_extract(row: dict) -> RowExtract:
    import os
    from app.core.config import settings

    rel = row["file_path"]
    full = os.path.join(settings.DOWNLOAD_PATH, rel)
    scope_id = row.get("scope_id") or 0

    if os.path.isdir(full):
        # 图集:目录下多个 jpg → 前缀形态 sb://library/{scope}/album/{rid}/
        # 逐文件上传,file_path 存前缀(尾斜杠),清单进 metadata。
        return RowExtract(
            file_path=rel,
            id=row["id"],
            sync_parent=row.get("version_number") == row.get("current_version"),
            is_album=True,
            scope_id=scope_id,
        )
    # 普通视频文件:内容寻址单对象
    return RowExtract(
        file_path=rel,
        id=row["id"],
        sync_parent=row.get("version_number") == row.get("current_version"),
        is_album=False,
        scope_id=scope_id,
    )
```

⚠️ `RowExtract` 现有字段需扩展：加 `is_album: bool = False` 和 `scope_id: int = 0`。改 `RowExtract` 的 dataclass 定义（`storage_migration.py:78`），加这两个可选字段，不影响现有 uploads/project_files。

- [ ] **Step 3：写失败测试（图集展开 + 视频单对象两条路径）**

`backend/tests/test_storage_migration_downloads.py`：

```python
"""downloads module:web 下载迁 S3,图集展开为前缀形态。

2026-07-12 首次 dry-run 因把图集目录当文件处理,70/880 行 IsADirectoryError。
本 module 显式判 isdir,图集走前缀展开,视频走单对象。
"""

import os

import pytest

from app.workflows.storage_migration import _downloads_extract


def test_video_file_is_single_object(tmp_path, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "video.mp4").write_bytes(b"x" * 16)
    ex = _downloads_extract({"file_path": "web/video.mp4", "id": 1,
                             "version_number": 1, "current_version": 1, "scope_id": 5})
    assert ex.is_album is False
    assert ex.scope_id == 5


def test_album_dir_is_prefix_form(tmp_path, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))
    d = tmp_path / "album123"
    d.mkdir()
    (d / "7613_0.jpg").write_bytes(b"a")
    (d / "cover.jpg").write_bytes(b"b")
    ex = _downloads_extract({"file_path": "album123", "id": 2,
                             "version_number": 1, "current_version": 1, "scope_id": 5})
    assert ex.is_album is True
```

- [ ] **Step 4：实现让测试通过**

改 `RowExtract` 加字段、注册 `_MODULES["downloads"]`、`_migrate_row` 里对 `is_album` 分支调用图集逐文件上传（用 `ObjectStore.put_dir` + 前缀 key）。**实现要求**：图集上传复用 `ObjectStore.put_dir`（已存在，在 `HlsPublisher` 验证过），前缀 key 用 `{scope}/album/{rid}/`，`update_row` 把 `file_path` 写成该前缀（尾斜杠）。

- [ ] **Step 5：跑测试**

```bash
cd backend && uv run pytest tests/test_storage_migration_downloads.py -v
```

Expected：2 passed。

- [ ] **Step 6：提交**

```bash
git add -A backend
git commit -m "feat(migration): downloads module 含图集展开为前缀形态

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

### Task 5：hls module

**Files:**
- Modify: `backend/app/workflows/storage_migration.py`（新增 `_MODULES["hls"]`）
- Test: `backend/tests/test_storage_migration_hls.py`

**Interfaces:**
- Consumes: `HlsPublisher.publish`（已有，PR2 验证过 6/6）、`hls_key`
- Produces: `_MODULES["hls"]`，把 97 个文件系统 HLS 目录迁到 `sb://library/hls/{rid}/{vid}/`

- [ ] **Step 1：写 SELECT + extract**

选 `resource_versions.hls_path` 非 NULL 且非 sb:// 的行。extract 从 `hls_path`（`derived/hls/{rid}/{vid}/master.m3u8`）反推 rid/vid，迁移调 `HlsPublisher.publish(hls_dir, base, rid, vid)`（在 `HLS_OBJECT_STORE=True` 下会走对象存储分支）。

```python
_HLS_SELECT_SQL = """
    SELECT rv.id, rv.resource_id, rv.version_number, rv.hls_path
    FROM resource_versions rv
    WHERE rv.hls_path IS NOT NULL AND rv.hls_path NOT LIKE 'sb://%'
      AND rv.storage_status = 'ok'
    ORDER BY rv.id LIMIT :limit
"""
```

- [ ] **Step 2：写失败测试**

`backend/tests/test_storage_migration_hls.py`：

```python
"""hls module:把文件系统 HLS 目录迁到对象存储,复用 HlsPublisher。

HlsPublisher 的对象存储路径已在 PR2 验证(master.m3u8 最后上传的顺序
不变量、产物完整性)。本 module 只负责逐行调它。
"""

from app.workflows.storage_migration import _hls_extract


def test_hls_extract_derives_rid_vid():
    ex = _hls_extract({
        "id": 1, "resource_id": 100, "version_number": 1,
        "hls_path": "derived/hls/100/200/master.m3u8",
    })
    assert ex.hls_rid == "100"
    assert ex.hls_vid == "200"
```

- [ ] **Step 3：实现 + 跑测试 + 提交**

实现 `_hls_extract`（正则从 `derived/hls/{rid}/{vid}/master.m3u8` 抽 rid/vid，加到 `RowExtract`），注册 module，`_migrate_row` 的 hls 分支调 `HlsPublisher().publish(...)` 并 update `hls_path`。

```bash
cd backend && uv run pytest tests/test_storage_migration_hls.py -v
git add -A backend && git commit -m "feat(migration): hls module 复用 HlsPublisher 迁移文件系统 HLS

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

### Task 6：derived module + sprite 端点去分叉（修 458 个 404 bug）

**Files:**
- Modify: `backend/app/workflows/storage_migration.py`（`_MODULES["derived"]`）
- Modify: `backend/app/api/resources_crud_router.py:795`（`serve_preview_sprite` 去分叉）
- Test: `backend/tests/test_sprite_serve_dispatch.py`

**Interfaces:**
- Produces: `_MODULES["derived"]` 迁缩略图/sprite/封面；sprite 端点支持 sb:// 且不再对 NULL file_path 早退 404

- [ ] **Step 1：先修 sprite bug（这是本会话实证的 458 个 web 视频 404）**

现状（`resources_crud_router.py:808`）：

```python
file_path = resource.get("file_path")
if not file_path:
    raise HTTPException(status_code=404, detail="No file path")   # ← bug:NULL 就 404
```

改为：**derived sprite 只需 resource_id，不该被 file_path 早退挡住**：

```python
        file_path = resource.get("file_path")
        from app.core.config import settings

        # derived sprite 只靠 resource_id 定位,不依赖 file_path —— 所以先探它,
        # 不要因 file_path 为空(web 视频真实路径在 parsed_media)就早退 404。
        derived_sprite = (
            Path(settings.DOWNLOAD_PATH) / "derived" / "thumbnails"
            / str(resource_id) / "preview_sprite.jpg"
        )
        if derived_sprite.exists():
            return FileResponse(path=str(derived_sprite), media_type="image/jpeg")

        # sb:// 前缀形态:sprite 在对象存储,签名 URL 302
        if file_path and file_path.startswith("sb://"):
            loc = resolve_media_source(file_path)
            # ...走 StreamingResponse 或签名 URL(参考 resources_versions_router 的 sb:// 分支)

        if not file_path:
            raise HTTPException(status_code=404, detail="No sprite")
        # 文件系统 next-to-source 兜底(旧逻辑保留)
        ...
```

- [ ] **Step 2：写测试证明修复（file_path=NULL 但 derived sprite 存在 → 200）**

`backend/tests/test_sprite_serve_dispatch.py`：

```python
"""sprite 端点去分叉:file_path=NULL 的 web 视频,只要 derived sprite 存在
就该 200,而不是被 `if not file_path: 404` 早退挡掉。

本会话实证:458 个 file_path=NULL 的 web 视频全部 404,即使 sprite 存在。
"""

import pytest
# 用 FastAPI TestClient + 造一个 file_path=NULL 的 resource + derived sprite,
# 断言端点返回 200 而非 404。（具体 fixture 依赖既有 test client 装配,
# 实现时参考 tests/ 里现有的 resources_crud 路由测试。）
```

**实现要求**：此测试需要既有的 test client fixture 与 ResourcesRepository mock。执行时先 grep `tests/` 找现有 resources 路由测试的装配方式照抄，不要另起炉灶。核心断言：`file_path=NULL` + `derived/thumbnails/{rid}/preview_sprite.jpg` 存在 → HTTP 200。

- [ ] **Step 3：derived module 迁移缩略图/sprite/封面**

`_MODULES["derived"]` 扫 `DOWNLOAD_PATH/derived/thumbnails/*/` 与 web 视频 next-to-source 的 sprite/cover，迁到 `sb://library/derived/{rid}/`。派生产物没有独立 DB 行，用一张映射或直接按 resource_id 前缀组织。**实现要求**：先确认派生产物的 DB 记录方式（可能只在磁盘、无 DB 行），决定是否需要新表或按前缀约定。这一步的具体形态取决于运行时勘察，实现前先跑一遍 `find /app/downloads/derived` 摸清结构。

- [ ] **Step 4：跑测试 + 端到端 + 提交**

```bash
cd backend && uv run pytest tests/test_sprite_serve_dispatch.py tests/test_thumbnail_lazy.py -v
# 端到端:取一个 file_path=NULL 的 web 视频,打 sprite 端点确认不再 404
git add -A backend && git commit -m "fix(sprite): 端点去分叉修 458 个 web 视频 404 + derived 迁移 module

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

# PR-4 执行存量迁移（非代码，gated on dry-run）

**这一步不能预先脚本化 —— 它由 dry-run 的实际输出驱动。** 每个 module 先 dry-run 出清单，核对数字，再正式迁，最后校验，最后才回收空间。

### Task 7：分模块执行迁移

- [ ] **Step 1：三个 module 各跑 dry-run，核对清单**

```bash
# 每个 module: dry_run=True, delete_source=False
# 期望数字(2026-07-28 基线): downloads 787 / hls 97 / derived ~800 sprite
```

Expected：dry-run 报的「可迁 / 跳过 / 失败」与基线吻合。**失败数 > 0 必须逐个查清再继续**（图集 isdir 判断、95 个失效行是否已被 storage_status 挡住）。

- [ ] **Step 2：先标记失效行（让迁移跳过）**

```bash
# 95 个失效行(PR #1601 后剩的 + 5 个 BV)标 storage_status='source_missing'
# 精确条件:文件系统路径 stat 不到的 web 行
```

- [ ] **Step 3：正式迁移，delete_source=False（只复制）**

按 downloads → hls → derived 顺序，`dry_run=False, delete_source=False`。**本地文件全部保留**，此时 DB 的 file_path 已指向 S3、本地也还在（双份）。

- [ ] **Step 4：抽样 sha256 校验（本地 vs S3）**

```bash
# 每个 module 抽 10%,比对本地文件与对象存储的 sha256
# 本会话已有此类脚本模式(smoke.py 的字节等价性)
```

Expected：抽样 100% 一致。**不一致则停止，不进 Step 5。**

- [ ] **Step 5：端到端播放/显示冒烟**

- 取迁移后的视频,走 `/media/fetch` 或播放端点,确认能播
- 取图集,前端确认 slides 正常显示（不只 API 200）
- 取 HLS,master.m3u8 + 分片能取回
- 95 个失效行:前端显示"文件已丢失"而非无限加载

- [ ] **Step 6：确认无误后，单独一轮 delete_source=True 回收空间**

```bash
# 仅在 Step 4/5 全绿后。delete_source=True 删本地文件,不可逆。
# 分批,每批后复查对象存储仍可读。
```

### Task 8：修下载链路 file_type 写入

**Files:**
- Modify: 下载链路写 `resources.file_type` 的地方（`0/4/68/2/51` 是坏值，应为 `video/image/audio`）
- Test: 断言新下载的 file_type 是正确枚举值

- [ ] **Step 1-5：** 定位写 file_type 的点（grep `file_type.*=` in download.py / downloader.py），改成从 mime 派生正确值，加测试断言，端到端验证新下载的 file_type 正确，提交。既有 458 个坏值可选一并 UPDATE 修正（低风险，纯枚举值订正）。

---

## 完成标准

- [ ] PR-1 合入：新下载 file_path 是 sb://，文件系统不再增长
- [ ] PR-2 合入：`is_prefix` + `storage_status` 就绪，读取端能处理前缀形态
- [ ] PR-3 合入：三个迁移 module 齐备，sprite 端点 458 个 404 修复
- [ ] PR-4 完成：787 源 + 97 HLS + sprite 全部 sb://，本地回收，端到端冒烟全绿
- [ ] `resource_versions` 里 `file_path NOT LIKE 'sb://%'` 的行只剩 `storage_status='source_missing'` 的失效行
- [ ] CIFS 上业务成品清空（除失效行的孤儿文件）

## 关键纪律（每个 PR 都适用）

1. **复制 + 校验 + 后置删除，绝不 move** —— 29GB 不可逆，必须有回退窗口
2. **先接线（PR-1）后迁移（PR-4）** —— 否则迁移期间新下载继续落文件系统
3. **成品进 S3，中间临时文件走本地盘** —— 转码/缩略图生成的临时产物不迁
4. **每个 PR 端到端写入冒烟** —— 读正常 ≠ 服务正常（CLAUDE.md 血泪）
5. **migration 编号从 393 起** —— 384 已被占用两次
