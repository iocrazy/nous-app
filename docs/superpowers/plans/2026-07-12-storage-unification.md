# 存储统一（Storage Unification）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 上传 resources / project_files / storyboard 产物的新写入统一到 Supabase Storage `library` 桶（`sb://library/t{scope}/{sha[:2]}/{sha[2:4]}/{sha}{ext}`），存量按行渐进迁移，前端零改动。

**Architecture:** 沿用已上线的 chat-media 双轨模式放大：`file_path` 列混存 legacy 相对路径与 `sb://` 值，`resolve_media_source()` 是唯一解释点；写路径经统一 `store_local_file()` 入口（sha256 内容寻址 + 去重 + 失败回落文件系统）；读路径经新共享层 `media_serving.py`（fs → FileResponse，sb → Range 流式代理/302 签名 URL）；工具链经 `materialize()` 拉临时文件。存量迁移是行级幂等的 DBOS workflow。

**Tech Stack:** FastAPI + storage3（Supabase Storage SDK，经现有 `ObjectStore` 包装）+ DBOS workflow + pytest。

**Spec:** `docs/superpowers/specs/2026-07-12-storage-unification-design.md`（已批准 2026-07-12）

## Global Constraints

- 总 bucket 名：`library`；key 形态：`t{scope_id}/{sha256[:2]}/{sha256[2:4]}/{sha256}{ext}`（全部经 `content_key`/`content_key_from_sha` 生成，禁止手拼）。
- flag：`FEATURE_UNIFIED_STORAGE`（默认 `False`）；关 = 行为零变化。
- 禁止散落 `startswith("sb://")` —— 一切位置解释走 `resolve_media_source()`（code review 红线）。
- 写失败必须回落文件系统 + `logger.warning`，上传永不因 storage-api down 硬失败。
- 下载链路（parsed_media）/ HLS 段 / sideload / inspiration 一概不动。
- HLS、缩略图等**衍生产物留在文件系统**；只有原件进 Storage。
- 后端 lint gate：改动的 `.py` 跑 `black --check` + `isort --check` + `flake8`（push 前）。
- task_tracking 纪律（CLAUDE.md 路线 C）：迁移 workflow 用 manager API，失败 `raise` 不 `return failed dict`。
- 提交走仓库惯例：每 PR 用 `/ship`；PR ≤1 天寿命。

## PR 序列与 ops 闸门

| 序 | 内容 | 闸门 |
|---|---|---|
| **OPS-0** | NAS：`GLOBAL_S3_BUCKET` `media`→`nous` 改名 | 必须先于任何 flag 开启 |
| **PR-1** | 地基：mig 359 + flag + `store_local_file`/`materialize` + `media_serving.py`（flag-dark，行为零变化） | — |
| **PR-2** | uploads 写路径 + resources 读端 + postprocess 适配 | dev 冒烟后才开 dev flag |
| **PR-3** | project_files + storyboard 写路径与读端（废 `NAS_BASE_PATH`） | 同上 |
| **PR-4** | 存量迁移 DBOS workflow + admin 触发端点 | dry-run 先行 |
| **OPS-1** | dev 栈全链冒烟 → prod flag on → 错误漏斗核查 | — |
| **PR-5** | 收官：generated_media flag 收编、legacy 删码、P3 退役评估、`thumbnails` 桶清理 | 各模块 legacy 行清零后 |

---

## OPS-0：物理顶层改名（NAS 手工，先于一切 flag）

- [ ] **Step 1: 改名**（SSH 到 NAS，port 1122，docker 必 sudo 绝对路径）

```bash
sudo docker stop $(sudo docker ps -qf name=storage)          # 短暂停写；chat 上传有 fs 回落兜底
mv /volume2/sources/MediaHub.library/object-storage/media \
   /volume2/sources/MediaHub.library/object-storage/nous
# Portainer/compose 中 storage 服务 env 改 GLOBAL_S3_BUCKET: nous
sudo docker compose up -d --no-deps storage                   # 铁律 --no-deps，别级联漂移栈
```

- [ ] **Step 2: 冒烟** — 前端拉一张已有 chat 生成图（走 `sb://chat-media/` 读端）+ 后端 `RUN_STORAGE_SMOKE=1 uv run pytest tests/test_storage_object_store_smoke.py -v` 全绿。
- [ ] **Step 3: 两个栈都做**（sb-prod / sb-dev；dev 栈同样有 `media/` 前缀则一并改）。

---

## PR-1：地基（flag-dark）

### Task 1.1: migration 359 — `library` 桶 + 清理 `thumbnails` 休眠桶

**Files:**
- Create: `supabase/migrations/359_library_bucket.sql`

**Interfaces:**
- Produces: `storage.buckets` 中 id=`library` 的 private 桶（后续所有任务的 PUT 目标）。

- [ ] **Step 1: 写 migration**

```sql
-- 359_library_bucket.sql
-- Storage unification (spec 2026-07-12): one master bucket for uploads /
-- project_files / storyboard originals. Private — service key only.
INSERT INTO storage.buckets (id, name, public)
VALUES ('library', 'library', false)
ON CONFLICT (id) DO NOTHING;

-- Cleanup: mig 052's thumbnails bucket has been dormant (0 objects) since the
-- thumbnail workload moved to filesystem+nginx. Guard: only drop when empty.
DELETE FROM storage.buckets b
WHERE b.id = 'thumbnails'
  AND NOT EXISTS (SELECT 1 FROM storage.objects o WHERE o.bucket_id = 'thumbnails');
```

- [ ] **Step 2: 本地 apply 验证**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -f supabase/migrations/359_library_bucket.sql
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres \
  -c "SELECT id, public FROM storage.buckets ORDER BY id;"
```
Expected: `library | f` 存在；`thumbnails` 不在（本地为空桶）。

- [ ] **Step 3: Commit** — `git commit -m "feat(storage): library bucket migration + drop dormant thumbnails bucket"`

### Task 1.2: flag `FEATURE_UNIFIED_STORAGE`

**Files:**
- Modify: `backend/app/core/config.py`（`FEATURE_CHAT_MEDIA_OBJECT_STORE` 定义块之后，约 :128）

- [ ] **Step 1: 加 flag**（跟随现有 Field/description 风格）

```python
    FEATURE_UNIFIED_STORAGE: bool = Field(
        default=False,
        description="Route new library writes (resource uploads, project_files, "
        "storyboard originals) to the Supabase Storage `library` bucket "
        "(sb:// paths) instead of the filesystem. Off (default) = every write "
        "stays on the filesystem (current behavior). Readers auto-resolve both "
        "path shapes via resolve_media_source, so flipping on is forward-only "
        "and rollback (flag off) keeps already-written sb:// rows readable. "
        "Flip only after OPS-0 (GLOBAL_S3_BUCKET=nous rename) and mig 359 are "
        "done on the target stack.",
    )
```

- [ ] **Step 2: Commit** — `git commit -m "feat(storage): FEATURE_UNIFIED_STORAGE flag (default off)"`

### Task 1.3: `media_storage.py` 扩展 — `library_store` / `sha256_file` / `store_local_file` / `materialize`

**Files:**
- Modify: `backend/app/services/library/media_storage.py`
- Modify: `backend/app/services/library/generated_media_service.py`（`_sha256_file` 收编，改 import）
- Test: `backend/tests/test_media_storage_unified.py`（新建）

**Interfaces:**
- Consumes: 现有 `ObjectStore` / `content_key` / `content_key_from_sha` / `to_file_path` / `resolve_media_source`。
- Produces（后续所有任务依赖的精确签名）:
  - `LIBRARY_BUCKET: str = "library"`；`def library_store() -> ObjectStore`
  - `ObjectStore.bucket` property（暴露只读桶名）
  - `def sha256_file(path: str) -> str`（同步，调用方 `asyncio.to_thread` 包）
  - `async def store_local_file(*, scope_id: int, source_path: str, mime: str, filename: Optional[str] = None, sha256: Optional[str] = None, store: Optional[ObjectStore] = None) -> StoredObject`
  - `@dataclass(frozen=True) class StoredObject: file_path: str; size_bytes: int; sha256: str`
  - `materialize(file_path: str)` — async context manager，yield `Path`（fs 行直接给真路径不清理；sb 行拉临时文件用完删）

- [ ] **Step 1: 写失败测试**

```python
"""Unified-storage foundation tests — pure logic + mocked ObjectStore."""
import pytest

from app.services.library.media_storage import (
    LIBRARY_BUCKET,
    StoredObject,
    library_store,
    materialize,
    resolve_media_source,
    sha256_file,
    store_local_file,
)


def test_library_store_targets_library_bucket():
    assert LIBRARY_BUCKET == "library"
    assert library_store().bucket == "library"


def test_sha256_file_matches_hashlib(tmp_path):
    import hashlib

    p = tmp_path / "a.bin"
    p.write_bytes(b"hello unified storage")
    assert sha256_file(str(p)) == hashlib.sha256(b"hello unified storage").hexdigest()


@pytest.mark.asyncio
async def test_store_local_file_dedup_skips_put(tmp_path, monkeypatch):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x" * 32)

    class FakeStore:
        bucket = "library"
        puts: list = []

        async def exists(self, key):
            return True  # already present → dedup

        async def put_file(self, key, path, mime):
            self.puts.append(key)

    fake = FakeStore()
    stored = await store_local_file(
        scope_id=42, source_path=str(p), mime="video/mp4", store=fake
    )
    assert isinstance(stored, StoredObject)
    assert stored.file_path.startswith("sb://library/t42/")
    assert stored.size_bytes == 32
    assert fake.puts == []  # dedup: no PUT
    # sb:// path round-trips through the resolver
    loc = resolve_media_source(stored.file_path)
    assert loc.is_object_store and loc.bucket == "library"


@pytest.mark.asyncio
async def test_store_local_file_puts_when_missing(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"png-bytes")

    class FakeStore:
        bucket = "library"

        def __init__(self):
            self.puts = []

        async def exists(self, key):
            return False

        async def put_file(self, key, path, mime):
            self.puts.append((key, path, mime))

    fake = FakeStore()
    stored = await store_local_file(
        scope_id=7, source_path=str(p), mime="image/png", store=fake
    )
    assert len(fake.puts) == 1
    assert fake.puts[0][0].endswith(".png")
    assert stored.sha256 in stored.file_path


@pytest.mark.asyncio
async def test_materialize_filesystem_yields_download_path(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/1/uploads/9/v1/a.txt"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_text("hi")
    async with materialize(rel) as local:
        assert local == f
    assert f.exists()  # fs path is NOT cleaned up


@pytest.mark.asyncio
async def test_materialize_object_store_pulls_temp_and_cleans(monkeypatch):
    import app.services.library.media_storage as ms

    async def fake_stream(self, key, **kw):
        yield b"chunk1"
        yield b"chunk2"

    monkeypatch.setattr(ms.ObjectStore, "get_stream", fake_stream)
    seen = {}
    async with materialize("sb://library/t1/ab/cd/abc.png") as local:
        seen["path"] = local
        assert local.read_bytes() == b"chunk1chunk2"
        assert local.suffix == ".png"
    assert not seen["path"].exists()  # temp cleaned up
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run pytest tests/test_media_storage_unified.py -v
```
Expected: FAIL — `ImportError: cannot import name 'store_local_file'` 等。

- [ ] **Step 3: 实现**（`media_storage.py` 追加；`ObjectStore` 加 property）

```python
# ── ObjectStore 内新增 ──
    @property
    def bucket(self) -> str:
        return self._bucket
```

```python
# ── 模块尾部追加（chat_media_store 之后）──

# Canonical bucket for the unified library (spec 2026-07-12): resource
# uploads, project_files, storyboard originals. chat-media stays separate.
LIBRARY_BUCKET = "library"


def library_store() -> ObjectStore:
    return ObjectStore(LIBRARY_BUCKET)


def sha256_file(path: str) -> str:
    """Streaming sha256 of a local file (sync — wrap in asyncio.to_thread)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class StoredObject:
    """Result of a unified-storage write: the sb:// value to persist + facts."""

    file_path: str
    size_bytes: int
    sha256: str


async def store_local_file(
    *,
    scope_id: int,
    source_path: str,
    mime: str,
    filename: Optional[str] = None,
    sha256: Optional[str] = None,
    store: Optional[ObjectStore] = None,
) -> StoredObject:
    """Content-address a local file into the library bucket (dedup PUT).

    The ONE write entrypoint for unified storage: sha256 (reuse the caller's
    precomputed hash when given — uploads already hash while streaming) →
    content key → skip-PUT if present → return the sb:// file_path to persist.
    Raises on storage failure; the CALLER owns the filesystem fallback.
    """
    import asyncio
    import os

    target = store or library_store()
    sha = sha256 or await asyncio.to_thread(sha256_file, source_path)
    key = content_key_from_sha(
        scope_id=scope_id, sha=sha, mime=mime, filename=filename
    )
    size = os.path.getsize(source_path)
    if not await target.exists(key):
        await target.put_file(key, source_path, mime)
    return StoredObject(
        file_path=to_file_path(target.bucket, key), size_bytes=size, sha256=sha
    )


@asynccontextmanager
async def materialize(file_path: str) -> AsyncIterator["Path"]:
    """Yield a REAL local Path for any file_path shape (the ffmpeg adapter).

    filesystem row → the actual path under DOWNLOAD_PATH (not cleaned up);
    sb:// row → streamed to a temp file, deleted on exit. Tooling (ffprobe,
    HLS transcode, thumbnails, promote) uses this instead of touching
    DOWNLOAD_PATH directly, so it works for both shapes.
    """
    import os
    import tempfile
    from pathlib import Path

    from app.core.config import settings

    loc = resolve_media_source(file_path)
    if not loc.is_object_store:
        yield Path(settings.DOWNLOAD_PATH) / loc.rel_path
        return
    store = ObjectStore(loc.bucket)
    fd, tmp = tempfile.mkstemp(suffix=Path(loc.key).suffix)
    os.close(fd)
    try:
        import aiofiles

        async with aiofiles.open(tmp, "wb") as out:
            async for chunk in store.get_stream(loc.key):
                await out.write(chunk)
        yield Path(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
```

顶部 import 区补：`from contextlib import asynccontextmanager`（`Path` 保持函数内 lazy import，维持模块"无重依赖可导入"约定）。

同时 `generated_media_service.py`：删除私有 `_sha256_file`，改 `from app.services.library.media_storage import sha256_file`，两处调用点（`:183` / `:210` 附近）改名。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && uv run pytest tests/test_media_storage_unified.py tests/ -k "media_storage or generated_media" -v
```
Expected: 全 PASS（含 generated_media 既有套件，确认 `_sha256_file` 收编无破坏）。

- [ ] **Step 5: Commit** — `git commit -m "feat(storage): store_local_file + materialize unified-write foundation"`

### Task 1.4: `media_serving.py` — 共享读端（fs / 流式代理 / 302）

**Files:**
- Create: `backend/app/services/library/media_serving.py`
- Modify: `backend/app/api/generated_media_router.py`（抽取其 Range 流式代理逻辑到新模块并回用——DRY，行为不变）
- Test: `backend/tests/test_media_serving.py`（新建）

**Interfaces:**
- Consumes: `resolve_media_source` / `ObjectStore.get_size` / `get_stream` / `signed_url`；`settings.STORAGE_SIGNED_URL_PUBLIC_BASE`。
- Produces: `async def serve_stored_file(file_path: str, *, mime: str, request: Request, disposition: str = "inline", extra_headers: Optional[dict] = None) -> Response`
  - fs 行 → `FileResponse`（404 缺文件）；
  - sb 行 + `STORAGE_SIGNED_URL_PUBLIC_BASE` 非空 → 302 `RedirectResponse`（签名 URL host 改写为该 base）；
  - sb 行否则 → `StreamingResponse` + `Range` 透传（复用 generated_media_router 已上线逻辑，含 206/416 语义）。

- [ ] **Step 1: 写失败测试** — 三分支：fs 存在/缺失、sb 无 base 走流代理（mock `ObjectStore.get_size`/`get_stream`，带 `Range: bytes=0-3` 断言 206 + `Content-Range`）、sb 有 base 断言 302 且 Location 以 base 开头。测试骨架同 Task 1.3 风格（monkeypatch settings + FakeStore），此处省略重复样板，四个用例名：
  `test_serve_fs_file` / `test_serve_fs_missing_404` / `test_serve_sb_stream_proxy_range` / `test_serve_sb_redirect_when_public_base`。
- [ ] **Step 2: 跑测试确认失败** — `uv run pytest tests/test_media_serving.py -v` → ImportError。
- [ ] **Step 3: 实现** — 把 `generated_media_router.py:75-158` 的 Range 解析 + StreamingResponse 组装逻辑**移动**到 `media_serving.py`（函数 `_range_stream_response(store, key, mime, request) -> Response`），`serve_stored_file` 按上述三分支组装；`generated_media_router` 改调 `serve_stored_file`，删除本地副本。302 分支：

```python
    if settings.STORAGE_SIGNED_URL_PUBLIC_BASE:
        signed = await store.signed_url(loc.key, ttl_seconds=300)
        public = settings.STORAGE_SIGNED_URL_PUBLIC_BASE.rstrip("/")
        # signed URL is LAN SUPABASE_URL-based; swap scheme+host for the public base
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(signed)
        base = urlsplit(public)
        url = urlunsplit((base.scheme, base.netloc, parts.path, parts.query, ""))
        return RedirectResponse(url, status_code=302, headers=extra_headers or {})
```

- [ ] **Step 4: 跑测试确认通过** — 新套件 + generated_media 读端既有套件全绿。
- [ ] **Step 5: Commit** — `git commit -m "feat(storage): media_serving shared reader (fs/stream-proxy/302)"`

### Task 1.5: PR-1 收口

- [ ] lint gate：`cd backend && uv run black --check app tests && uv run isort --check app tests && uv run flake8 app tests`（只看改动文件相关报错）
- [ ] 全量 `uv run pytest` 绿
- [ ] `/ship` 开 PR：`feat(storage): unified storage foundation (flag-dark)` — 说明"行为零变化，flag 默认关"

---

## PR-2：uploads 写路径 + resources 读端

### Task 2.1: `upload_resource` 双轨写

**Files:**
- Modify: `backend/app/services/library/resources_service.py:146-170`
- Test: `backend/tests/test_resources_unified_storage.py`（新建）

**Interfaces:**
- Consumes: `store_local_file`（Task 1.3）；既有 `stream_upload_to_disk` 已返回 `(size, sha256)`——哈希免费复用。
- Produces: flag on 时 `resources.file_path` / `file_versions.file_path` = `sb://library/...`。

- [ ] **Step 1: 写失败测试** — stub `ResourcesRepository`（内存 dict）+ FakeStore，断言：flag on → create_version 收到的 `file_path` 以 `sb://library/t{scope}` 开头、tmp 文件已清理；flag off → 现行 `teams/{scope}/uploads/...` 不变；FakeStore.put 抛异常 → 回落 fs 路径 + 文件真实落盘。
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现** — 替换 146-162 行的"move 到 save_dir"段：

```python
        stored = None
        if settings.FEATURE_UNIFIED_STORAGE:
            try:
                stored = await store_local_file(
                    scope_id=int(scope_id),
                    source_path=str(tmp_path),
                    mime=mime,
                    filename=safe_name,
                    sha256=file_hash,
                )
            except Exception as exc:
                logger.warning(
                    f"[upload_resource] unified-storage write failed, falling "
                    f"back to filesystem: scope={scope_id} error={exc!r}"
                )
        if stored is not None:
            relative_path = stored.file_path
        else:
            # Move the streamed file into teams/{scope_id}/uploads/{id}/v1/
            save_dir = (
                Path(settings.DOWNLOAD_PATH)
                / "teams" / scope_id / "uploads" / resource_id / "v1"
            )
            save_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, str(tmp_path), str(save_dir / safe_name))
            relative_path = f"teams/{scope_id}/uploads/{resource_id}/v1/{safe_name}"
```

（`finally: tmp_path.unlink(missing_ok=True)` 原样保留——object-store 成功时它负责清 tmp。）

- [ ] **Step 4: 确认通过** + **Step 5: Commit** — `feat(storage): resource upload dual-track write`

### Task 2.2: `upload_new_version` 双轨写

**Files:**
- Modify: `backend/app/services/library/resources_service.py:205-280`
- Test: 同上文件追加用例

- [ ] **Step 1-2: 失败测试** — 同 2.1 三断言，针对版本路径。
- [ ] **Step 3: 实现** — 现代码直接流写最终目录；重构为与 2.1 同构：先 `stream_upload_to_disk` 到 `tempfile.mkstemp` 路径拿 `(size, hash)`，flag on 先试 `store_local_file`（复用 hash），失败/flag off 时按原 `base_relative/v{n}/{safe_name}` 落盘（move）。`base_relative` 推导逻辑（223-238 行）**只在 fs 回落分支里执行**——sb:// 行没有目录语义。注意 `existing_path` 可能已是 `sb://`：`"/v" in existing_path` 判定在 sb 行恒 False → 自然走 `resource_items` scope 分支，行为正确，加一条测试钉住。
- [ ] **Step 4-5: 通过 + Commit** — `feat(storage): resource version upload dual-track write`

### Task 2.3: resources 读端走 `serve_stored_file`

**Files:**
- Modify: `backend/app/api/resources_crud_router.py`（file 端点 :505-529、cover 端点 :687-716、及同文件其余 `FileResponse(Path(settings.DOWNLOAD_PATH)/...)` 处——grep 清点）
- Modify: `backend/app/api/resources_versions_router.py`（版本文件下载端点；HLS 段端点**不动**）
- Test: `backend/tests/test_resources_serving.py`（新建）

- [ ] **Step 1: 清点读端**

```bash
grep -n "DOWNLOAD_PATH" backend/app/api/resources_crud_router.py backend/app/api/resources_versions_router.py
```
把每一处归类：原件读 → 改；HLS/缩略图（衍生物，恒为 fs 相对路径）→ 不改。

- [ ] **Step 2: 失败测试** — sb:// 行经 `/resources/{id}/file` 返回 206/302（按 base 配置）；legacy 行为回归不变；`maybe_direct_redirect`（P3 nginx）只对 legacy 行调用。
- [ ] **Step 3: 实现** — file 端点 505-524 行替换为：

```python
        if not file_path:
            raise HTTPException(status_code=404, detail="No file available")
        cache_headers = {"Cache-Control": "private, no-store"} if token else {}
        loc = resolve_media_source(file_path)
        if not loc.is_object_store:
            redirect = await maybe_direct_redirect(loc.rel_path)  # P3, legacy only
            if redirect is not None:
                return redirect
        return await serve_stored_file(
            file_path,
            mime=resource.get("mime_type", "application/octet-stream"),
            request=request,
            extra_headers=cache_headers,
        )
```

cover 端点同构（懒缩略图生成的**源文件读取**改 `materialize`；缩略图输出路径不变）。versions_router 的版本原件下载同构。

- [ ] **Step 4-5: 通过 + Commit** — `feat(storage): resources readers via serve_stored_file`

### Task 2.4: postprocess / promote / 转码适配（materialize）

**Files:**
- Modify: `backend/app/workflows/upload_postprocess.py`（ffprobe/Pillow 元数据、缩略图生成——源读取包 `materialize`）
- Modify: `backend/app/services/media/transcode/transcode_service.py:239-266`（HLS 输出目录：源是 sb:// 时不能再用 `{source.parent}/hls`，改 `teams/{scope}/derived/{resource_id}/v{n}/hls/`，`hls_path` 列照旧存 fs 相对路径）
- Modify: `backend/app/services/library/promote_generated_media_service.py`（`shutil.copy2` → `materialize(src)` + `store_local_file`/fs 回落，目标从 chat-media 升入 library）
- Test: 各自既有套件 + 新用例

- [ ] **Step 1: 清点** — `grep -rn "DOWNLOAD_PATH" backend/app/workflows/upload_postprocess.py backend/app/services/media/transcode/ backend/app/services/library/promote_generated_media_service.py`，逐处判定"读原件（改）/写衍生物（不改）"。
- [ ] **Step 2: 失败测试** — sb:// 源的 ffprobe 元数据提取走 temp 文件（mock get_stream 提供最小 mp4 头不现实——stub `materialize` 返回 fixture 文件，测**编排**：postprocess 对 sb:// 行调用了 materialize 而非直拼 DOWNLOAD_PATH）；HLS 输出目录对 sb:// 源落 `derived/` 树。
- [ ] **Step 3: 实现模式**（每个读点同构）：

```python
        async with materialize(resource["file_path"]) as local_path:
            metadata = await extract_media_metadata(str(local_path))
```

- [ ] **Step 4-5: 通过 + Commit** — `feat(storage): tooling reads via materialize; HLS derived dir for sb rows`

### Task 2.5: PR-2 收口

- [ ] 全量 pytest + lint gate 绿；`/ship` 开 PR：`feat(storage): uploads write/read via unified storage (flag-dark)`
- [ ] **dev 栈冒烟**（flag on dev `.env` + `stop -t0`/`start`）：上传图片/视频 → 列表/预览/播放（Range 拖动）→ 传新版本 → 封面 → HLS 转码完成 → promote 一张生成图 → `application_logs` 无新 ERROR。

---

## PR-3：project_files + storyboard

### Task 3.1: `projects_service.upload_file` 双轨写（顺手补 scope）

**Files:**
- Modify: `backend/app/services/library/projects_service.py:516-609`
- Test: `backend/tests/test_projects_unified_storage.py`（新建）

**Interfaces:**
- Consumes: `store_local_file`；project 的 scope 从 `project["team_id"]`（若列名不同，先 `SELECT column_name FROM information_schema.columns WHERE table_name='projects'` 核列——CLAUDE.md 口径）。

- [ ] **Step 1-2: 失败测试** — flag on → `project_files.file_path`/version 均 `sb://library/t{scope}`；flag off → `mediatrack/{project_id}/...` 不变；storage 异常 → fs 回落。
- [ ] **Step 3: 实现** — 与 Task 2.2 同构：流到 mkstemp（拿 size+hash，顺带把现在 `file_size, _ =` 丢掉的 hash 用起来）→ flag on 试 `store_local_file(scope_id=int(project["team_id"]), ...)` → 回落分支保留现有"重名加 `_counter`"逻辑（仅 fs 需要；内容寻址天然无重名问题）。ffprobe 元数据段（:578）在 store 前用本地 tmp 路径执行——文件还在手上，无需 materialize。
- [ ] **Step 4-5: 通过 + Commit** — `feat(storage): project_files dual-track write`

### Task 3.2: project_files 读端

**Files:**
- Modify: `grep -rn "mediatrack\|DOWNLOAD_PATH" backend/app/api/projects_router.py backend/app/services/library/projects_service.py` 清点出的 serve/读点（含 :700,731 的另一子路径）
- Test: 同 3.1 文件追加

- [ ] 模式同 Task 2.3（serve 端点 → `serve_stored_file`；工具读 → `materialize`），逐点带回归测试后 Commit：`feat(storage): project_files readers via serve_stored_file`

### Task 3.3: storyboard 写路径 + 读端（废 `NAS_BASE_PATH`）

**Files:**
- Modify: `backend/app/services/storyboard/storyboard_service.py`（`_ensure_nas_directories` :142-165、asset 写点 :568-629、preview/split 写点 :715-766）
- Modify: `backend/app/services/storyboard/storyboard_export_service.py`（读点）
- Modify: storyboard 相关 router 的 serve 端点（`grep -rn "NAS_BASE_PATH" backend/app/` 全量清点，目标=0 残留）
- Test: `backend/tests/test_storyboard_unified_storage.py`（新建）

- [ ] **Step 1: 全量清点**

```bash
grep -rn "NAS_BASE_PATH" backend/app/ | tee /tmp/nas_base_sites.txt
```
每一处归类：原件写（→ `store_local_file`）/原件读（→ `materialize` 或 `serve_stored_file`）/目录预建（→ 删除，object store 无目录）。

- [ ] **Step 2: 失败测试** — flag on 生成 storyboard 图 → `storyboard_assets.file_path` = `sb://library/t{team}`；flag off 走 `settings.DOWNLOAD_PATH`（**不再读 `NAS_BASE_PATH` env**——回落路径也统一到 settings，这是本任务顺手清的配置源债）；preview 生成对 sb:// 源走 materialize。
- [ ] **Step 3: 实现** — 写点同构 Task 2.1 模式；`_ensure_nas_directories` 保留但仅在 fs 回落分支调用且改用 `settings.DOWNLOAD_PATH`；splits/previews 若认定为**衍生物**（可再生）→ 留 fs（与 HLS 同策略），只有原图进 library——在 PR 描述里明示这个判定。
- [ ] **Step 4-5: 通过 + Commit** — `feat(storage): storyboard originals via unified storage; retire NAS_BASE_PATH`

### Task 3.4: PR-3 收口

- [ ] `grep -rn "NAS_BASE_PATH" backend/ docker/` = 0（compose 里的 env 行留待 OPS-1 顺手删，PR 描述注明）
- [ ] 全量 pytest + lint gate；`/ship`：`feat(storage): project_files + storyboard on unified storage (flag-dark)`
- [ ] dev 冒烟：项目传文件 → 预览；storyboard 生成 → 画布显示 → 导出。

---

## PR-4：存量迁移 workflow

### Task 4.1: `storage_migration` DBOS workflow

**Files:**
- Create: `backend/app/workflows/storage_migration.py`
- Modify: `backend/app/workflows/_dispatch_bundle.py`（注册）
- Test: `backend/tests/test_storage_migration.py`（新建）

**Interfaces:**
- Produces: `storage_migration_workflow(module: str, scope_id: Optional[int], limit: int, dry_run: bool, delete_source: bool)`；模块注册表：

```python
# 每模块声明：迁移目标行的 SELECT、行→(scope_id, mime, filename) 的取数、UPDATE 语句。
# file_versions 行是迁移单元（每版本一个物理文件）；resources.file_path 在其
# current_version 行迁完后同步。project_files 同构。storyboard_assets 直接行级。
_MODULES = {
    "uploads": ...,        # file_versions + resources 同步
    "project_files": ...,  # project 侧 file_versions + project_files 同步
    "storyboard": ...,     # storyboard_assets (+ video_assets)
}
```

- [ ] **Step 1: 失败测试**（stub repo + FakeStore + tmp fixture 文件）：
  - 单行幂等：同一行跑两遍 → 第二遍 skip（`file_path` 已 `sb://`）；
  - dry_run：PUT 发生（或 exists 短路）但**不 UPDATE 不删文件**；
  - 验证失败protect：`get_size` 与本地 size 不符 → 该行 `raise`、不 UPDATE、不删；
  - `delete_source=False`：迁移后原文件保留；
  - scope 过滤：只迁 `scope_id` 匹配的行。
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现** — 行处理核心（在 `@DBOS.step` 内，**不在 step 内 dispatch 别的 workflow**——既有教训）：

```python
async def _migrate_row(row, module_cfg, *, dry_run: bool, delete_source: bool) -> str:
    rel = row["file_path"]
    if rel.startswith("sb://"):
        return "skipped"
    local = Path(settings.DOWNLOAD_PATH) / rel
    if not local.exists():
        logger.warning(f"[storage-migration] missing local file, skip: {rel}")
        return "missing"
    scope_id, mime, filename = module_cfg.extract(row)
    stored = await store_local_file(
        scope_id=scope_id, source_path=str(local), mime=mime, filename=filename
    )
    # verify: object really there and sized right, BEFORE touching the DB row
    size = await library_store().get_size(
        stored.file_path.removeprefix("sb://library/")
    )
    if size != local.stat().st_size:
        raise RuntimeError(f"size mismatch after PUT: {rel} {size} != {local.stat().st_size}")
    if dry_run:
        return "dry_run_ok"
    await module_cfg.update_row(row["id"], stored.file_path, stored.sha256)
    if delete_source:
        local.unlink(missing_ok=True)
    return "migrated"
```

  workflow 循环：`SELECT ... WHERE file_path NOT LIKE 'sb://%' [AND scope…] ORDER BY id LIMIT :limit`，逐行 try/except（单行失败计数不中断批次，**批次尾若 failed>0 则 raise** 让 task_tracking 记 failed）；task_tracking 经 manager API `create/start/update_progress/complete`，进度 = 处理行数/批大小。
- [ ] **Step 4-5: 通过 + Commit** — `feat(storage): row-wise idempotent migration workflow`

### Task 4.2: admin 触发端点

**Files:**
- Modify: `backend/app/api/admin_router.py`（或 modules 注册表所在 admin 端点文件——以 `grep -rn "admin" backend/app/api/ | grep -i router` 定位既有 admin 面）
- Test: 直接调函数测（admin 端点单测直调函数非 TestClient——既有惯例）

- [ ] `POST /api/v1/admin/storage-migration`，body `{module, scope_id?, limit=500, dry_run=true, delete_source=false}`，校验 module ∈ 注册表、admin 权限沿用该文件既有依赖注入；dispatch workflow 返回 `workflow_id`。默认值刻意保守（dry_run=true）。
- [ ] Commit：`feat(storage): admin endpoint to dispatch storage migration`

### Task 4.3: PR-4 收口

- [ ] 全量 pytest + lint gate；`/ship`：`feat(storage): legacy migration workflow + admin trigger`

---

## OPS-1：上线序列（NAS + prod）

- [ ] 前置核对：OPS-0 已完成（`GLOBAL_S3_BUCKET=nous`）；mig 359 已随 CI apply（`SELECT id FROM storage.buckets WHERE id='library'`）
- [ ] dev 栈：flag on → PR-2/PR-3 冒烟清单全绿
- [ ] prod：host `docker/.env` 加 `FEATURE_UNIFIED_STORAGE=true` → `stop -t0`/`start`（Watchtower 不读 compose）
- [ ] canary：上传→播放→storyboard 生成→project 传文件；错误漏斗 `SELECT module,message,COUNT(*) FROM application_logs WHERE level='ERROR' AND logged_at>=NOW()-INTERVAL '1 day' GROUP BY 1,2 ORDER BY 3 DESC`
- [ ] 存量迁移：admin 端点按模块 dry_run → 真跑（先 `delete_source=false` 一批抽查 → 后续批次 `true`）；顺序 storyboard → project_files → uploads（先小后大）
- [ ] 顺手：compose 删 `NAS_BASE_PATH` env 行（需 NAS `docker compose up -d`，Watchtower 不管 compose）

---

## PR-5：收官（各模块 legacy 清零后，按需拆多个小 PR）

- [ ] **验证查询**（每模块）：`SELECT COUNT(*) FROM file_versions WHERE file_path NOT LIKE 'sb://%'` 等 = 0
- [ ] generated_media 收编：`FEATURE_CHAT_MEDIA_OBJECT_STORE` 达成 2 周 flag 寿命 → 默认改 true→删 flag 与 fs 分支（保留超限回落）；可选把 chat-media 存量并入 library（跑同一迁移 workflow 的 `chat_media` 模块配置 + copy）
- [ ] 删各 service 的 fs 写回落分支？**不删**——回落是永久的可用性兜底（spec 风险表）；删的是 flag 判断（flag 常开 → 直接走 unified 写 + 回落）
- [ ] `FEATURE_UNIFIED_STORAGE` flag 删除（2 周寿命规则）
- [ ] P3 nginx 直出退役评估：legacy 全清后 `/f/` 路径无消费者 → 删 `nginx_direct.py` + nginx conf + `system_settings.nginx_direct_serve`（单独小 PR）
- [ ] 更新 `docs/architecture/2026-07-06-adr-object-storage-vs-filesystem.md` 状态为 SUPERSEDED（指向本 spec）
- [ ] 记忆收尾：更新 `reference_adr_object_storage.md` 与 MEMORY.md RESUME POINT

---

## Self-Review 记录

1. **Spec 覆盖**：范围表 4 迁 4 不迁 ✓（uploads=PR-2、project_files/storyboard=PR-3、generated_media 收官=PR-5；不迁项在 Global Constraints 钉死）；路径方案 ✓（Task 1.3 key 全经 content_key）；路由表 ✓（Task 1.4/2.3 三分支）；`materialize` 适配 ✓（Task 2.4）；迁移 job ✓（PR-4 行级幂等+dry-run+验证后删）；ops（nous 改名/冒烟/flag）✓（OPS-0/OPS-1）；清理项 thumbnails/NAS_BASE_PATH/P3 ✓（Task 1.1/3.3/PR-5）。
2. **占位符**：Task 1.4 Step 1 与 3.x 的部分测试给的是用例名+断言要点而非全文——属"模式已在 1.3/2.1 全文给出，同构展开"，实现者信息完整；无 TBD。
3. **类型一致性**：`store_local_file`/`StoredObject`/`materialize`/`serve_stored_file` 签名在 Interfaces 与代码块间逐字一致 ✓；`sha256_file` 同步 + to_thread 约定前后一致 ✓。
4. **已知不确定点（实现时核，不阻塞计划）**：`projects.team_id` 列名待 information_schema 核；storyboard splits/previews 衍生物判定在 Task 3.3 内显式决策；admin router 文件名以 grep 定位。
