# Generated Media Object-Store Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** generated_media（Tier-1 画板/生成媒体）**只写对象存储**，本地文件系统写入路径退役；i2v/参考图解析支持 `sb://` 行（消灭"参考图静默失效"存量 bug 和"大图能 i2v 小图不能"怪象）。

**用户决策（2026-07-28）:** "只存对象存储,本地的遗弃"。遗弃 = 不再写本地、存量本地行不迁移不删除（读/服务路径继续兼容）。

**Architecture:** ① `register_generated_media` flag-on 时移除两个 filesystem fallback：超限图片改走视频同款的 stream/put_file 路径（16MiB 内存上限只再决定"内存 or 临时文件"，不再决定"对象存储 or 本地"）；对象存储写失败**直接 raise**（fail loudly，不再静默降级）。flag-off 保留纯 filesystem 路径（无对象存储的 dev 环境用）。② `resolve_generated_media_local_path` 重构为 async contextmanager `generated_media_local_path`：filesystem 行 yield 真路径，`sb://` 行经 `materialize()` 临时落盘 yield 临时路径、退出清理；三个调用点全部迁移。

**Tech Stack:** FastAPI 后端 only，零前端/零 migration。

**关键已核实事实（直接引用）:**
- `backend/app/services/library/generated_media_service.py`：`register_generated_media` L234；`_write_local_generation_to_object_store` 视频分支已是 sha256_file + put_file 流式模式（照抄给大图用）；`_OBJECT_STORE_IMAGE_MAX_BYTES = 16MiB`（L152）；`resolve_generated_media_local_path` L489（`sb://` 行返 None 即 bug 现场）
- `materialize()` = `app/services/library/media_storage.py:515` 的 `@asynccontextmanager`，两种 file_path 形态都吃、finally 必删临时文件（Phase 3 已两处使用）
- 3 个调用点同构（`path = await resolve...(url)` → provider 调用）：`app/workflows/canvas_generation.py:61-70`、`app/workflows/script_shot_video.py:67-71`、`app/workflows/canvas_timeline.py:78-81`
- `_write_generation_to_object_store`（URL 版）视频同样走临时文件流式；图片 `_read_url_capped` 内存版

## Global Constraints

- 只在 `feature/generated-media-objstore-only` 分支、`.worktrees/gm-objstore-only` 工作区内工作；不切分支、不碰主仓库目录
- 后端提交前 `uv run black/isort/flake8` 三关全过（CI gate）
- 存量本地行为准绳：**读路径一行不改**（`_serve_media_row`/`filesystem_response` 保持）
- 每 task 一 commit + trailer `Claude-Session: https://claude.ai/code/session_017MjbKQdeuX9c91L5xdhbns`

---

### Task 1: register 只写对象存储（TDD）

**Files:**
- Modify: `backend/app/services/library/generated_media_service.py`
- Test: `backend/tests/test_generated_media_objstore_only.py`（新建）

**Interfaces:**
- `_write_local_generation_to_object_store` / `_write_generation_to_object_store`：图片分支加"超过 `_OBJECT_STORE_IMAGE_MAX_BYTES` → 改走视频同款 temp-file/put_file 流式"分支（URL 版先 `_download_to` 临时文件再走文件版逻辑），16MiB 从"降级触发器"变"内存/磁盘缓冲选择器"
- `register_generated_media`：flag-on 时删除 `except Exception → file_path=None` 的静默降级——写失败 `raise`；flag-off 保持纯 filesystem 路径。docstring 更新（删掉 "Any failure...falls back" 段，写明 object-store only + 失败即失败）

- [ ] **Step 1 失败测试**（mock `chat_media_store()` 返回 fake store；参照 `tests/test_generated_media_import_from_resource.py` 的 mock 层级）：
  - flag-on + 小图 local source → put_bytes 调用、file_path 以 `sb://` 开头、无任何 DOWNLOAD_PATH 写入
  - flag-on + **大图**（写一个 >cap 的临时文件）local source → put_file 调用（流式路径）、仍 `sb://`、不降级
  - flag-on + store.put_bytes raise → `register_generated_media` **向上抛**（pytest.raises），DB insert 未被调用
  - flag-off → 走 filesystem、file_path 为相对路径（现状回归锚点）
- [ ] **Step 2** 跑 `cd backend && uv run pytest tests/test_generated_media_objstore_only.py -v` → FAIL
- [ ] **Step 3 实现**（大图流式：local 版直接 `sha256_file`+`put_file`——其实与视频分支相同，可把条件改为 `kind=='video' or size>cap`；URL 版先 `_download_to(tmp)` 再复用 local 版逻辑，finally 删 tmp）
- [ ] **Step 4** 测试 PASS + 既有 `tests/test_generated_media_import*.py` 无回归 + black/isort/flake8
- [ ] **Step 5** Commit `feat(storage): generated_media 只写对象存储 — 移除 filesystem 降级,大图走流式`

---

### Task 2: 解析器支持 sb:// + 三调用点迁移（TDD）

**Files:**
- Modify: `backend/app/services/library/generated_media_service.py`（新 cm + 删旧函数）
- Modify: `backend/app/workflows/canvas_generation.py`、`backend/app/workflows/script_shot_video.py`、`backend/app/workflows/canvas_timeline.py`
- Test: `backend/tests/test_generated_media_local_path_cm.py`（新建）

**Interfaces:**
- Produces:

```python
@asynccontextmanager
async def generated_media_local_path(
    url: str, *, media_kind: str = "image"
) -> AsyncIterator[Optional[str]]:
    """Yield a readable local path for a durable generated-media URL.

    filesystem row → the real path (no cleanup); sb:// row → materialize()
    temp file (deleted on exit); any miss → None. Replaces
    resolve_generated_media_local_path, whose object-store rows returned
    None and silently degraded i2v to text2video.
    """
```

- 旧 `resolve_generated_media_local_path` **删除**（全仓 3 个调用点本 task 内全部迁移；grep 确认无残留）
- 调用点迁移形态（三处同构）：

```python
async with generated_media_local_path(source_url, media_kind="image") as image_path:
    result = await provider.generate_video(..., image_path=image_path)
```

（provider 调用必须在 `async with` 块内——materialize 的临时文件在退出时删除。canvas_timeline / script_shot_video 按各自缩进套同样结构；先读每处上下文，别破坏返回值组装。）

- [ ] **Step 1 失败测试**：filesystem 行 yield 真路径且退出后文件仍在；`sb://` 行（mock `ObjectStore.get_stream`）yield 存在的临时文件、块退出后文件已删；URL 不匹配/行不存在/kind 不符 → yield None；containment guard 保留（恶意 rel_path yield None）
- [ ] **Step 2** FAIL → **Step 3 实现**（内部复用 `materialize(row["file_path"])`，filesystem 分支保留现有 realpath 收敛 + isfile 检查语义）
- [ ] **Step 4** 三个 workflow 迁移 + `grep -rn resolve_generated_media_local_path app/` 为空；相关 workflow 既有测试无回归（`uv run pytest tests -k "canvas_generation or shot_video or timeline" -q`）
- [ ] **Step 5** 全部 PASS + lint 三关 → Commit `feat(storage): 参考图解析支持 sb:// — materialize 临时落盘,i2v 全通`

---

### Task 3: 全量验证 + 交付

- [ ] `uv run pytest tests/test_generated_media_objstore_only.py tests/test_generated_media_local_path_cm.py tests/test_generated_media_import_from_resource.py tests/test_generated_media_import.py -q` 全过；`tests -k "generated_media" -q` 无回归
- [ ] lint 三关对全部触碰文件
- [ ] ledger + push + PR（base master；描述写明：生产 flag 已是 true 故行为立即生效；存量本地行只读不迁；合并后验收 = 画板生成一张图确认 file_path 为 sb:// + 连参考图跑一次 i2v 确认真的用上了图）

## Self-Review 记录

- 用户决策映射：只写对象存储（T1）、本地遗弃=不迁移+读兼容（Global Constraints 准绳）、i2v 打通（T2）
- 一致性：`generated_media_local_path` 名称/签名 T2 内自洽；T1 的"cap 变缓冲选择器"与 T2 无接口耦合
- 风险声明：写失败不再静默降级——对象存储故障时生成任务会显式失败（用户决策的直接后果，docstring + PR 描述写明）
