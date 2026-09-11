# 画布编辑器接受任意图片引用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 画布上的裁剪 / 扩图 / 切图（以及已经能用的遮罩 / 画笔 / 缩放 / 放大）对**任何**能显示在画布上的图片都可用，不再要求图片先被"入库"成 `resources` 行；产物落在画布自己的 scope 的生成内容（Tier-1）里。

**Architecture:** 新增画布作用域的派生端点 `POST /api/v1/canvases/{canvas_id}/derive-{crop,grid,outpaint}`，入参是图片 URL（`/api/v1/generated-media/{id}/…` 或 `/api/v1/resources/{id}/cover|file`），后端按 URL 形状取字节并做**来源读权限**校验，复用现有纯像素变换，结果用 `register_generated_media` 注册进画布 scope。前端两个编辑入口（`OutputNodeView`、`MediaItemEditor`）改为按"正在编辑的那张图的 URL"派生，删除打开编辑器即自动 promote 的副作用。`canvas_resource_refs` 改为"跟着归档走"：输出节点显示的 generation 若已归档成 resource，就算作 output 引用。

**Tech Stack:** FastAPI + SQLAlchemy ORM（`read_scope`）+ Pillow（后端）；React + Vitest + Testing Library（前端）；pytest（`asyncio_mode="auto"`）。

**Spec:** 本计划的设计部分即 spec（与用户在会话中定稿："这也就是素材引用的问题吧……不要管我素材来自于哪里"）。见下方「Design」D1–D9。

## Global Constraints

- 与用户沟通、计划散文用中文；代码、UI 文案、测试数据用英文（CLAUDE.md「UI 语言规范」「测试数据」）。
- 每个 PR 一个 worktree，**从 `origin/master` 建**：`git -C "$REPO" worktree add "$REPO/../nous-app-wt-<slug>" -b <branch> origin/master`；之后 `cd backend && uv sync` / `cd frontend && npm ci`。写操作一律 `git -C <绝对路径>`。
- 纯 refactor PR（PR1）不夹带逻辑改动，24 小时内合并；feature PR 基于已合并 refactor 的 master。
- 后端提交前三件套全过：`uv run black <files>`、`uv run isort <files>`、`uv run ruff check <files>`，再跑相关 pytest。只跑 ruff 不跑 black 曾让 CI 真红（PR #2212）。
- 前端提交前：`npx vitest run <files>`、`npx tsc --noEmit -p .`、`npx eslint <files>`。
- 新代码禁止 `text()` 裸 SQL；查询用 ORM `select`。
- Snowflake id 在 JSON 里一律字符串（`str(id)`）。
- 边界 mock 用真实形状：成功体 `{"success": true, "data": …}`；错误体是 `ErrorResponse` 外壳 `{"success": false, "error": <文案>, "code": "http_<status>", "request_id": …, "details": …}`。
- 用户动作触发的路径必须类型化失败回显，不许 silent no-op；前端 catch 必须 `console.error` 且给用户可见错误。
- 临时文件：私有目录（`tempfile.TemporaryDirectory()` 默认 0700）+ `tempfile.mkstemp(dir=…)`。
- 不把 session token 写进画布数据（`nodes_json`）。
- Python 类型注解用 `X | None`；值对象 `@dataclass(frozen=True)`；接口用 `typing.Protocol`。
- 本机已知假红（非本计划引起，以 CI 为准）：`tests/api/test_distribution_*` 12 个、`tests/test_storage_migration_web_resource_files.py`。
- 发布顺序：PR1 → PR2 → PR3 → PR4 → Task 12 真栈验收 → PR5。PR5 只在 PR4 已上线且 grep 证明旧调用方为零后开工。
- 仓库 private + GitHub Free，CI 托管 runner 可能计费假红（`runner_name` 为空、`steps=0`）；是否切 public 需用户当次授权。

---

## 现状（为什么要改）

| 编辑动作 | 现在走哪 | 对输入的要求 | 问题 |
|---|---|---|---|
| 画笔 / 遮罩 / 缩放 | 浏览器烘焙 → `POST /generated-media/import` | 无 | 缩放用的是 `preview_url`，编辑网格里第 2 张图时缩放的是第 1 张 |
| 放大 | `POST /generated-media/{id}/upscale` | generated-media id | 结果注册进**调用者个人空间**；源图**没有任何读权限校验** |
| 裁剪 / 扩图 / 切图 | `POST /resources/{resource_id}/derive-*` | 必须是 `resources` 行 | 生成图要先 promote 入库（打开编辑器即触发，把"未审阅"推进成"已保存"，还在我的上传里多出一份）；产物是 `resources` 行，不进生成内容 |

生产数据：画布上所有可显示图片引用都已归一成 `/api/v1/generated-media/{id}/cover`（资源库素材经 `import-from-resource` 进画布）。所以"按 URL 派生"覆盖了全部真实形状，`resources/{id}/cover` 只是兼容兜底。

## Design

- **D1 编辑器按"正在编辑的图"的 URL 派生。** `OutputNodeView` 用 `editSourceUrl = editingUrl ?? primaryImageUrl`，`MediaItemEditor` 用 `item.url`。节点上的 `resource_id` 不再参与任何编辑判断。这顺带修掉"网格双击第 2 张图、却按第 1 张图派生"的缺陷。
- **D2 产物是 Tier-1。** 注册为 `generated_media`：`origin_kind='canvas_upload'`，`params.role='derived'`，`params.op ∈ {crop, grid, outpaint}`，`params.derived_from=<source url>`，`derivation_kind=<op>`；scope 用 `canvas_generation._registration_scope_id(canvas_id, user_id)`（与画布生成同一个 scope）。`derived` **不在** `INTERMEDIATE_ROLES`——用户主动要的产物，收件箱里可见。
- **D3 摆放交互不变。** 裁剪：原地替换 `preview_url`（并清掉旧 `crop_region`）；扩图：旁边新建节点 + 预填提示词节点；切图：按行列摆放新节点；`MediaItemEditor`：追加到卡片。
- **D4 来源读权限。** 写权限在画布（`_gate_canvas_write`）；读权限在来源：generated-media 源必须满足"其 scope 是调用者个人空间，或调用者是该 team 成员"（与 promote 同一个判定，抽成共享函数）；resources 源必须在画布 scope 内（复用 `resource_local_path` 的 scope 检查）；认不出的 URL 形状 → 422。
- **D5 画布资源引用跟着归档走。** `canvas_resource_refs` 的 `role='output'` 由两部分组成：遗留的 `data.resource_id`，加上输出节点正在显示的 generation（`preview_url` 与 `images[].url`）里 `promoted_resource_id` 非空的那些。修掉"promote 写入的引用在下一次保存被 `replace_for_canvas` 抹掉"的既有缺陷。
- **D6 不做遮罩抠图端点**（UI 从未调用）。放大改为：源 generation 必须可读，结果注册进**源 generation 自己的 scope**。
- **D7 兼容。** 旧节点上的 `crop_region` 照常渲染；旧节点的 `resource_id` 照常计入引用；只是编辑流程不再写这两个字段。
- **D8 旧端点下线在最后。** `/resources/{id}/derive-crop|derive-grid|derive-outpaint|derive-mask-cutout` 只在前端新版上线、真栈验收通过、grep 调用方为零之后删除；`/resources/{id}/split` 不动（不在本计划范围）。
- **D9 画布内导入进画布 scope。** `POST /generated-media/import` 带 `canvas_id` 时，先过画布写权限，再注册进 `_registration_scope_id`（遮罩 / 画笔 / 缩放产物与派生产物同一个 scope）；不带 `canvas_id` 的调用行为不变。

### 不在范围

- `/resources/{id}/split`、`split_derive_service`（无前端调用方，另议）。
- AI 扩图（`mode="ai"`）的真实工作流接入：保持现状（前端从不传 `mode`，默认 deterministic）。
- 已被自动 promote 过的存量 resources 的清理。

## File Map

| 文件 | 动作 | 职责 |
|---|---|---|
| `backend/app/services/canvas/crop_derive_service.py` | 改（PR1） | 抽出 `crop_image` 纯函数 |
| `backend/app/services/canvas/grid_derive_service.py` | 改（PR1） | 抽出 `GridTileImage` + `split_image_by_lines` |
| `backend/app/services/canvas/outpaint_derive_service.py` | 改（PR1） | 抽出 `extend_image`（含 AI 回退） |
| `backend/tests/test_derive_transforms.py` | 建（PR1） | 三个纯函数的测试 |
| `backend/app/services/library/generation_access.py` | 建（PR1） | `can_read_generation_scope` 共享读权限判定 |
| `backend/app/services/library/promote_generated_media_service.py` | 改（PR1） | `_can_read_source_scope` 委托给共享判定 |
| `backend/tests/services/library/test_generation_access.py` | 建（PR1） | 判定测试 |
| `backend/app/services/library/generated_roles.py` | 改（PR2） | 新增 `DERIVED` |
| `backend/app/services/canvas/canvas_material_source.py` | 建（PR2） | URL → 字节 + 读权限 + 类型化错误 |
| `backend/tests/test_canvas_material_source.py` | 建（PR2） | |
| `backend/app/services/canvas/canvas_derive_service.py` | 建（PR2） | 加载 → 变换 → 注册 Tier-1 |
| `backend/tests/test_canvas_derive_service.py` | 建（PR2） | |
| `backend/app/schemas/canvas_material_derive_schema.py` | 建（PR2） | 三个请求体 |
| `backend/app/api/canvas_derive_router.py` | 建（PR2） | 三个端点 |
| `backend/app/api/__init__.py` | 改（PR2） | 注册路由 |
| `backend/tests/test_canvas_derive_router.py` | 建（PR2） | |
| `backend/app/services/canvas/asset_refs.py` | 改（PR3） | 抽取输出节点 generation id + 合并归档引用 |
| `backend/app/repositories/generated_media_repository.py` | 改（PR3） | `promoted_resource_ids` |
| `backend/app/services/canvas/canvas_service.py` | 改（PR3） | `_sync_refs` 合并归档引用 |
| `backend/app/api/generated_media_router.py` | 改（PR3） | 放大按源 scope + 读校验；导入进画布 scope |
| `frontend/features/canvas-core/services/canvasService.ts` | 改（PR4 加 / PR5 删） | 新派生客户端；删旧客户端 |
| `frontend/features/canvas-core/smart/mediaEditBridge.ts` | 改（PR5） | 删 `ensureResourceId`，留 `genIdFromDurableUrl` |
| `frontend/features/canvas-core/smart/mediaImport.ts` | 改（PR4） | `CanvasUploadRole` 加 `'derived'` |
| `frontend/features/canvas-core/smart/swapEditedImage.ts` | 建（PR4） | 原地替换被编辑的那张图（纯函数） |
| `frontend/features/canvas-core/smart/nodes/OutputNodeView.tsx` | 改（PR4） | 按 URL 派生、删自动 promote |
| `frontend/features/canvas-core/smart/nodes/MediaItemEditor.tsx` | 改（PR4） | 同上 + 可见错误 |
| `backend/app/api/resources_crud_router.py` 等 | 删（PR5） | 旧派生端点与服务 |

## PR Map

| PR | 分支 | Tasks |
|---|---|---|
| PR1 | `refactor/canvas-derive-pure-transforms` | Task 1, 2 |
| PR2 | `feat/canvas-derive-any-reference-backend` | Task 3, 4, 5, 6 |
| PR3 | `fix/canvas-refs-follow-archive` | Task 7, 8, 9 |
| PR4 | `feat/canvas-editors-take-reference` | Task 10, 11 |
| — | 验收 | Task 12 |
| PR5 | `chore/remove-resource-derive-endpoints` | Task 13 |

---

## PR1 — `refactor/canvas-derive-pure-transforms`（纯重构，24h 内合并）

worktree：`git -C /Volumes/program/project-code/repos/nous-app worktree add /Volumes/program/project-code/repos/nous-app-wt-derive-refactor -b refactor/canvas-derive-pure-transforms origin/master`，然后 `cd …/backend && uv sync`。

### Task 1: 把三种像素变换从"资源派生服务"里抽成纯函数

目的：PR2 的画布派生服务要用同样的变换，但输入不是 `resources` 行。把"变换 + 错误映射"抽出来，旧的 `derive_*_resource` 改为调用它们，**对外行为逐字节不变**（现有测试一行不改、全部保持绿）。

变换留在各自模块里，不搬家：`tests/test_outpaint_derive_service.py` 用 `patch("app.services.canvas.outpaint_derive_service.run_outpaint_via_nous")` 打桩，搬走就打不到了。

**Files:**
- Modify: `backend/app/services/canvas/crop_derive_service.py`
- Modify: `backend/app/services/canvas/grid_derive_service.py`
- Modify: `backend/app/services/canvas/outpaint_derive_service.py`
- Create: `backend/tests/test_derive_transforms.py`

**Interfaces:**
- Consumes: `crop_normalized(image_bytes, region, *, mime_type=None) -> bytes`（`image_crop.py`）；`tiles_from_lines(*, xs, ys) -> list[GridTile]`、`GridSplitError`（`grid_split.py`）；`extend_canvas(image_bytes, padding, *, mime_type=None) -> bytes`、`OutpaintError`、`Padding`（`image_outpaint.py`）；`DeriveError(*, status_code, detail)`（`derive_persistence.py`）。
- Produces:
  - `crop_derive_service.crop_image(file_bytes: bytes, mime_type: str | None, region: CropRegion) -> bytes`，失败抛 `CropDeriveError(status_code=400, detail="crop failed: …")`
  - `grid_derive_service.GridTileImage(row: int, col: int, image_bytes: bytes)`（frozen dataclass）
  - `grid_derive_service.split_image_by_lines(file_bytes: bytes, mime_type: str | None, *, xs: Sequence[float], ys: Sequence[float]) -> list[GridTileImage]`，线非法抛 `GridDeriveError(400, str(exc))`，裁切失败抛 `GridDeriveError(400, "grid crop failed: …")`
  - `outpaint_derive_service.extend_image(file_bytes: bytes, mime_type: str | None, padding: Padding, *, prompt: str | None = None, mode: str = "deterministic", label: str = "") -> bytes`（async），padding 非法抛 `OutpaintDeriveError(400, str(exc))`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_derive_transforms.py`：

```python
"""Pure pixel transforms shared by the resource and canvas derive paths."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.services.canvas.crop_derive_service import CropDeriveError, crop_image
from app.services.canvas.grid_derive_service import (
    GridDeriveError,
    GridTileImage,
    split_image_by_lines,
)
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding
from app.services.canvas.nous_center_runner import NousCenterNotConfigured
from app.services.canvas.outpaint_derive_service import (
    OutpaintDeriveError,
    extend_image,
)


def _png(w: int = 100, h: int = 50) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (10, 200, 50)).save(buf, format="PNG")
    return buf.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as img:
        return img.size


def test_crop_image_returns_the_region() -> None:
    out = crop_image(
        _png(100, 50), "image/png", CropRegion(x=0.0, y=0.0, width=0.5, height=1.0)
    )
    assert _size(out) == (50, 50)


def test_crop_image_maps_primitive_failure_to_400() -> None:
    with pytest.raises(CropDeriveError) as caught:
        crop_image(
            b"not an image",
            "image/png",
            CropRegion(x=0.0, y=0.0, width=0.5, height=0.5),
        )
    assert caught.value.status_code == 400
    assert caught.value.detail.startswith("crop failed:")


def test_split_image_by_lines_is_row_major() -> None:
    tiles = split_image_by_lines(_png(100, 50), "image/png", xs=[0.5], ys=[])
    assert [(t.row, t.col) for t in tiles] == [(0, 0), (0, 1)]
    assert all(isinstance(t, GridTileImage) for t in tiles)
    assert [_size(t.image_bytes) for t in tiles] == [(50, 50), (50, 50)]


def test_split_image_by_lines_rejects_no_lines_with_400() -> None:
    with pytest.raises(GridDeriveError) as caught:
        split_image_by_lines(_png(), "image/png", xs=[], ys=[])
    assert caught.value.status_code == 400
    assert caught.value.detail == "at least one split line is required"


def test_split_image_by_lines_maps_crop_failure_to_400() -> None:
    with pytest.raises(GridDeriveError) as caught:
        split_image_by_lines(b"not an image", "image/png", xs=[0.5], ys=[])
    assert caught.value.status_code == 400
    assert caught.value.detail.startswith("grid crop failed:")


async def test_extend_image_deterministic() -> None:
    out = await extend_image(
        _png(100, 50), "image/png", Padding(left=0.5, top=0.0, right=0.5, bottom=0.0)
    )
    assert _size(out) == (200, 50)


async def test_extend_image_rejects_zero_padding_with_400() -> None:
    with pytest.raises(OutpaintDeriveError) as caught:
        await extend_image(
            _png(), "image/png", Padding(left=0.0, top=0.0, right=0.0, bottom=0.0)
        )
    assert caught.value.status_code == 400


async def test_extend_image_ai_mode_falls_back_when_nous_unconfigured() -> None:
    with patch(
        "app.services.canvas.outpaint_derive_service.run_outpaint_via_nous",
        new=AsyncMock(side_effect=NousCenterNotConfigured("off")),
    ) as nous:
        out = await extend_image(
            _png(100, 50),
            "image/png",
            Padding(left=0.5, top=0.0, right=0.5, bottom=0.0),
            prompt="a windswept meadow",
            mode="ai",
            label="gen:5",
        )
    nous.assert_awaited_once()
    assert _size(out) == (200, 50)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_derive_transforms.py -v`
Expected: 收集阶段 FAIL，`ImportError: cannot import name 'crop_image'`。

- [ ] **Step 3: 实现 `crop_image`**

`crop_derive_service.py`，在 `_derived_filename` 之后加：

```python
def crop_image(
    file_bytes: bytes, mime_type: str | None, region: CropRegion
) -> bytes:
    """Crop encoded image bytes by a normalized region.

    Shared by the resource derive path and the canvas derive path; the
    ``400 crop failed: …`` mapping lives here so both answer the same way.
    """
    try:
        return crop_normalized(file_bytes, region, mime_type=mime_type)
    except Exception as exc:
        raise CropDeriveError(status_code=400, detail=f"crop failed: {exc}") from exc
```

把 `derive_crop_resource` 里的 try/except 块替换为：

```python
    source = await load_source_image(repo, source_resource_id)
    cropped = crop_image(source.file_bytes, source.mime_type, region)
```

- [ ] **Step 4: 实现 `GridTileImage` / `split_image_by_lines`**

`grid_derive_service.py`，在 `GridDeriveResult` 之后加：

```python
@dataclass(frozen=True)
class GridTileImage:
    row: int
    col: int
    image_bytes: bytes


def split_image_by_lines(
    file_bytes: bytes,
    mime_type: str | None,
    *,
    xs: Sequence[float],
    ys: Sequence[float],
) -> list[GridTileImage]:
    """Split encoded image bytes along normalized lines, row-major.

    Every tile is cropped in memory before returning, so a bad region fails
    the whole call before any caller persists a single tile.
    """
    try:
        tiles = tiles_from_lines(xs=list(xs), ys=list(ys))
    except GridSplitError as exc:
        raise GridDeriveError(status_code=400, detail=str(exc)) from exc
    try:
        return [
            GridTileImage(
                row=tile.row,
                col=tile.col,
                image_bytes=crop_normalized(
                    file_bytes, tile.region, mime_type=mime_type
                ),
            )
            for tile in tiles
        ]
    except Exception as exc:
        raise GridDeriveError(
            status_code=400, detail=f"grid crop failed: {exc}"
        ) from exc
```

`derive_grid_resources` 里从 `try: tiles = tiles_from_lines(...)` 到循环结束整段替换为：

```python
    source = await load_source_image(repo, source_resource_id)
    tile_images = split_image_by_lines(
        source.file_bytes, source.mime_type, xs=xs, ys=ys
    )

    prefix = filename_prefix or _DEFAULT_PREFIX
    results: list[GridTileResult] = []
    for tile in tile_images:
        new_resource = await persist_derived_image(
            repo,
            user_id=user_id,
            scope_id=source.scope_id,
            folder_id=source.folder_id,
            library_id=source.library_id,
            filename=_tile_filename(prefix, tile.row, tile.col, source.filename),
            image_bytes=tile.image_bytes,
            mime_type=source.mime_type,
        )
        results.append(
            GridTileResult(row=tile.row, col=tile.col, resource=new_resource)
        )

    return GridDeriveResult(
        rows=len({t.row for t in tile_images}),
        cols=len({t.col for t in tile_images}),
        tiles=results,
    )
```

- [ ] **Step 5: 实现 `extend_image`**

`outpaint_derive_service.py`：把 `_fill_via_ai_or_fallback(source, padding, prompt)` 整个替换为下面三个函数（日志文案保持 `source=<label>`，旧路径传 `label=str(source.resource.get("id"))`，日志逐字不变）：

```python
async def extend_image(
    file_bytes: bytes,
    mime_type: str | None,
    padding: Padding,
    *,
    prompt: str | None = None,
    mode: str = "deterministic",
    label: str = "",
) -> bytes:
    """Extend encoded image bytes by ``padding``.

    ``mode='ai'`` with a prompt tries nous-center and falls back to the
    deterministic blur fill on any failure; ``label`` only feeds the log line.
    """
    if prompt and mode == "ai":
        return await _fill_via_ai_or_fallback(
            file_bytes, mime_type, padding, prompt, label
        )
    return _deterministic_fill(file_bytes, mime_type, padding)


def _deterministic_fill(
    file_bytes: bytes, mime_type: str | None, padding: Padding
) -> bytes:
    try:
        return extend_canvas(file_bytes, padding, mime_type=mime_type)
    except OutpaintError as exc:
        raise OutpaintDeriveError(status_code=400, detail=str(exc)) from exc


async def _fill_via_ai_or_fallback(
    file_bytes: bytes,
    mime_type: str | None,
    padding: Padding,
    prompt: str,
    label: str,
) -> bytes:
    """Try AI fill; fall back silently to deterministic on any failure."""
    from app.core.config import settings

    try:
        image_bytes = await run_outpaint_via_nous(
            settings=settings,
            prompt=prompt,
            source_bytes=file_bytes,
            padding=padding,
            mime_type=mime_type or "image/png",
        )
        logger.info(
            f"outpaint AI path succeeded source={label} "
            f"slug_key={_OUTPAINT_SLUG_KEY}"
        )
        return image_bytes
    except Exception as exc:
        logger.info(
            f"outpaint AI unavailable, falling back to deterministic fill "
            f"source={label} reason={exc!r}"
        )

    return _deterministic_fill(file_bytes, mime_type, padding)
```

`derive_outpaint_resource` 里从 `image_bytes: bytes` 到 else 分支结束整段替换为：

```python
    source = await load_source_image(repo, source_resource_id)
    image_bytes = await extend_image(
        source.file_bytes,
        source.mime_type,
        padding,
        prompt=prompt,
        mode=mode,
        label=str(source.resource.get("id")),
    )
```

- [ ] **Step 6: 跑新测试 + 全部旧派生测试**

Run: `cd backend && uv run pytest tests/test_derive_transforms.py tests/test_crop_derive_service.py tests/test_grid_derive_service.py tests/test_outpaint_derive_service.py tests/test_mask_derive_service.py -v`
Expected: 全部 PASS；旧测试文件 `git diff --stat` 为零改动。

- [ ] **Step 7: 格式化 + lint + 提交**

```bash
cd backend
uv run black app/services/canvas/crop_derive_service.py app/services/canvas/grid_derive_service.py app/services/canvas/outpaint_derive_service.py tests/test_derive_transforms.py
uv run isort app/services/canvas/crop_derive_service.py app/services/canvas/grid_derive_service.py app/services/canvas/outpaint_derive_service.py tests/test_derive_transforms.py
uv run ruff check app/services/canvas/ tests/test_derive_transforms.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refactor add backend/app/services/canvas/crop_derive_service.py backend/app/services/canvas/grid_derive_service.py backend/app/services/canvas/outpaint_derive_service.py backend/tests/test_derive_transforms.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refactor commit -m "refactor(canvas): 裁剪/切图/扩图的像素变换抽成纯函数，资源派生服务改为调用它们"
```

### Task 2: 抽出"能否读这个 generation 的 scope"共享判定

目的：PR2 的派生端点、PR3 的放大端点都要问"调用者能不能读这张生成图"，答案必须与 promote 完全一致。现在它是 `PromoteGeneratedMediaService._can_read_source_scope` 私有方法；抽成模块级纯函数，promote 委托给它（行为不变）。

**Files:**
- Create: `backend/app/services/library/generation_access.py`
- Modify: `backend/app/services/library/promote_generated_media_service.py:93-111`
- Create: `backend/tests/services/library/test_generation_access.py`

**Interfaces:**
- Consumes: `ConversationRepository.is_team_member(*, team_id: int, user_id: str) -> bool`（`app/repositories/conversation_repository.py:200`）
- Produces:
  - `generation_access.TeamMembership`（Protocol：`async is_team_member(*, team_id: int, user_id: str) -> bool`）
  - `generation_access.can_read_generation_scope(gen: Mapping[str, Any], *, user_id: str, personal_team_id: int, membership: TeamMembership) -> bool`（async）

- [ ] **Step 1: 写失败测试**

`backend/tests/services/library/test_generation_access.py`：

```python
"""The one scope-read rule shared by promote, canvas derive and upscale."""

from __future__ import annotations

from app.services.library.generation_access import can_read_generation_scope


class FakeMembership:
    def __init__(self, teams: set[int]) -> None:
        self.teams = teams
        self.calls: list[tuple[int, str]] = []

    async def is_team_member(self, *, team_id: int, user_id: str) -> bool:
        self.calls.append((team_id, user_id))
        return team_id in self.teams


async def test_row_without_scope_is_unreadable() -> None:
    membership = FakeMembership({7})
    assert not await can_read_generation_scope(
        {"scope_id": None}, user_id="u1", personal_team_id=7, membership=membership
    )
    assert membership.calls == []


async def test_personal_scope_is_readable_without_asking_membership() -> None:
    membership = FakeMembership(set())
    assert await can_read_generation_scope(
        {"scope_id": "7"}, user_id="u1", personal_team_id=7, membership=membership
    )
    assert membership.calls == []


async def test_team_scope_is_readable_by_a_member() -> None:
    membership = FakeMembership({42})
    assert await can_read_generation_scope(
        {"scope_id": "42"}, user_id="u1", personal_team_id=7, membership=membership
    )
    assert membership.calls == [(42, "u1")]


async def test_team_scope_is_refused_for_a_non_member() -> None:
    assert not await can_read_generation_scope(
        {"scope_id": 42},
        user_id="u1",
        personal_team_id=7,
        membership=FakeMembership(set()),
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/services/library/test_generation_access.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.services.library.generation_access'`。

- [ ] **Step 3: 实现**

`backend/app/services/library/generation_access.py`：

```python
"""Who may read a generated_media row's scope.

One rule, three callers (promote, canvas derive, upscale). A second copy of
this check would be the place the two answers start to differ.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class TeamMembership(Protocol):
    async def is_team_member(self, *, team_id: int, user_id: str) -> bool: ...


async def can_read_generation_scope(
    gen: Mapping[str, Any],
    *,
    user_id: str,
    personal_team_id: int,
    membership: TeamMembership,
) -> bool:
    """May *user_id* read the scope this generation lives in?

    Works for every ``origin_kind`` — a chat_upload row carries the same
    ``scope_id`` as any other (the resolved chat scope). A row with no
    ``scope_id`` at all is unreadable: a check that cannot run has not passed.
    """
    gen_scope = gen.get("scope_id")
    if gen_scope is None:
        return False
    source_scope_id = int(gen_scope)
    if source_scope_id == personal_team_id:
        return True
    return bool(
        await membership.is_team_member(team_id=source_scope_id, user_id=user_id)
    )
```

`promote_generated_media_service.py`：顶部 import 加

```python
from app.services.library.generation_access import can_read_generation_scope
```

把 `_can_read_source_scope` 方法体替换为（签名不动，两处调用点不动）：

```python
    async def _can_read_source_scope(
        self, gen: dict, *, user_id: str, personal_team_id: int, conv_repo
    ) -> bool:
        """Delegates to the shared rule — see ``generation_access``."""
        return await can_read_generation_scope(
            gen,
            user_id=user_id,
            personal_team_id=personal_team_id,
            membership=conv_repo,
        )
```

- [ ] **Step 4: 跑新测试 + promote 全部测试**

Run: `cd backend && uv run pytest tests/services/library/test_generation_access.py tests/services/library/test_promote_target_scope.py tests/test_generated_media_promote_route.py -v`
Expected: 全部 PASS。再 `grep -rn "_can_read_source_scope\|promote" backend/tests -l | xargs uv run pytest -q` 兜一遍 promote 相关测试。

- [ ] **Step 5: 格式化 + lint + 提交**

```bash
cd backend
uv run black app/services/library/generation_access.py app/services/library/promote_generated_media_service.py tests/services/library/test_generation_access.py
uv run isort app/services/library/generation_access.py app/services/library/promote_generated_media_service.py tests/services/library/test_generation_access.py
uv run ruff check app/services/library/ tests/services/library/test_generation_access.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refactor add backend/app/services/library/generation_access.py backend/app/services/library/promote_generated_media_service.py backend/tests/services/library/test_generation_access.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refactor commit -m "refactor(generated): generation scope 读权限判定抽成共享函数，promote 委托调用"
```

- [ ] **Step 6: 开 PR1 并合并**

```bash
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refactor push -u origin refactor/canvas-derive-pure-transforms
gh pr create --repo iocrazy/nous-app --base master --head refactor/canvas-derive-pure-transforms \
  --title "refactor(canvas): 派生像素变换与 generation 读权限判定抽成共享函数" \
  --body "纯重构，无逻辑改动。为「画布编辑器接受任意图片引用」（docs/superpowers/plans/2026-09-10-canvas-derive-any-reference.md）铺路。旧派生服务测试零改动全绿。"
```

CI 绿即合并（`gh pr merge --squash --delete-branch`）；后端部署链 `deploy-gpu.yml` 跑完后 `ssh gpupc 'docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz'` 确认 ready。

---

## PR2 — `feat/canvas-derive-any-reference-backend`

前置：PR1 已合并。worktree：`git -C /Volumes/program/project-code/repos/nous-app worktree add /Volumes/program/project-code/repos/nous-app-wt-derive-backend -b feat/canvas-derive-any-reference-backend origin/master`，`cd …/backend && uv sync`。

### Task 3: 新增 `derived` 角色

**Files:**
- Modify: `backend/app/services/library/generated_roles.py`
- Create: `backend/tests/services/library/test_generated_roles_derived.py`

**Interfaces:**
- Produces: `generated_roles.DERIVED = "derived"`；`CANVAS_UPLOAD_ROLES` 含它，`INTERMEDIATE_ROLES` 不含它。

- [ ] **Step 1: 写失败测试**

```python
"""``derived`` is a product the user asked for — visible in the inbox."""

from app.services.library.generated_roles import (
    CANVAS_UPLOAD_ROLES,
    DERIVED,
    INTERMEDIATE_ROLES,
    normalize_role,
)


def test_derived_is_a_visible_canvas_upload_role() -> None:
    assert DERIVED == "derived"
    assert DERIVED in CANVAS_UPLOAD_ROLES
    assert DERIVED not in INTERMEDIATE_ROLES
    assert normalize_role(" derived ") == "derived"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/services/library/test_generated_roles_derived.py -v`
Expected: FAIL，`ImportError: cannot import name 'DERIVED'`。

- [ ] **Step 3: 实现**

`generated_roles.py`：在 `UPSCALE_RESULT = "upscale_result"` 下一行加

```python
DERIVED = "derived"
```

`CANVAS_UPLOAD_ROLES` 改为

```python
CANVAS_UPLOAD_ROLES = (USER_UPLOAD, MASK, BRUSH, REFERENCE, UPSCALE_RESULT, DERIVED)
```

`INTERMEDIATE_ROLES` 上方注释 "``upscale_result`` is deliberately NOT here" 那段末尾补一句：

```python
#: ``derived`` (a crop / grid tile / outpaint made in a canvas editor, or a
#: client-baked resize) is not here for the same reason.
```

模块 docstring 的 writer 列表末尾补一条：

```
* the canvas editors' crop / grid / outpaint derive and the client-baked
  resize (role ``derived``).
```

- [ ] **Step 4: 跑测试 + 既有 role 相关测试**

Run: `cd backend && uv run pytest tests/services/library/test_generated_roles_derived.py tests/test_generated_media_upscale_route.py -v`
Expected: PASS。

- [ ] **Step 5: 格式化 + lint + 提交**

```bash
cd backend
uv run black app/services/library/generated_roles.py tests/services/library/test_generated_roles_derived.py
uv run isort app/services/library/generated_roles.py tests/services/library/test_generated_roles_derived.py
uv run ruff check app/services/library/generated_roles.py tests/services/library/test_generated_roles_derived.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-backend add backend/app/services/library/generated_roles.py backend/tests/services/library/test_generated_roles_derived.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-backend commit -m "feat(generated): canvas_upload 新增可见角色 derived"
```

### Task 4: 画布素材加载器——URL → 字节，带来源读权限

**Files:**
- Create: `backend/app/services/canvas/canvas_material_source.py`
- Create: `backend/tests/test_canvas_material_source.py`

**Interfaces:**
- Consumes: `classify_reference_url(url) -> tuple[Literal["genmedia","resource","unknown"], int | None]`、`generated_media_local_path(url, *, media_kind="image")`（async CM，yield `str | None`）、`resource_local_path(url, *, scope_id, media_kind="image")`（async CM，yield `ResourceRefResolution(path | reason)`）——均在 `app/services/library/generated_media_service.py`；`GeneratedMediaRepository().get_by_id(gen_id) -> dict | None`；`_resolve_personal_team_id(user_id) -> str`（`app/services/library/resources_service.py`）；`get_conversation_repository()`（`app/repositories/conversation_repository.py`）；Task 2 的 `can_read_generation_scope`；`DeriveError`。
- Produces:
  - `canvas_material_source.DeriveInput(file_bytes: bytes, mime_type: str, label: str)`（frozen dataclass）
  - `canvas_material_source.load_canvas_material(url: str, *, user_id: str, canvas_scope_id: int) -> DeriveInput`（async）
  - `canvas_material_source.sniff_image_mime(data: bytes) -> str`（按字节判定编码格式，解不开抛 `DeriveError(400)`；Task 5 用它判定产物 mime）
  - 错误契约（全部 `DeriveError`）：认不出的形状 422；generation 不存在**或不可读** 404（不区分，避免存在性探测）；不是图片 / 解不开 400；文件取不到 502；超过 `MAX_SOURCE_BYTES`（64 MiB）413；resource 原因码 `unknown_shape` 422 / `not_in_scope` 403 / `no_image_file` 400 / `materialize_failed` 502。

设计备注：`classify_reference_url` 对**相对** URL 不去 query，而 resource 正则是 `$` 锚定，`/api/v1/resources/9/cover?token=…` 会被判成 unknown。所以加载器先把相对 URL 的 query / fragment 去掉；分类出 id 后用规范 URL（`…/file`）去取文件，调用方传来的后缀与 query 不再参与取文件。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_canvas_material_source.py`：

```python
"""Canvas image reference → bytes, with the SOURCE's read rule applied."""

from __future__ import annotations

import contextlib
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.services.canvas import canvas_material_source as cms
from app.services.canvas.derive_persistence import DeriveError
from app.services.library.generated_media_service import ResourceRefResolution


def _png_file(tmp_path: Path) -> str:
    path = tmp_path / "source.png"
    Image.new("RGB", (10, 8), (1, 2, 3)).save(path, format="PNG")
    return str(path)


class FakeGenRepo:
    def __init__(self, rows: dict[int, dict[str, Any]]) -> None:
        self.rows = rows

    async def get_by_id(self, gen_id: int) -> dict[str, Any] | None:
        return self.rows.get(gen_id)


class FakeMembership:
    def __init__(self, teams: set[int]) -> None:
        self.teams = teams

    async def is_team_member(self, *, team_id: int, user_id: str) -> bool:
        return team_id in self.teams


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    png = _png_file(tmp_path)
    state: dict[str, Any] = {
        "rows": {5: {"id": "5", "scope_id": "7", "media_kind": "image"}},
        "teams": set(),
        "gen_path": png,
        "gen_urls": [],
        "resource": ResourceRefResolution(path=png),
        "resource_calls": [],
    }

    async def personal_team(user_id: str) -> str:
        return "7"

    @contextlib.asynccontextmanager
    async def gen_path(url: str, *, media_kind: str = "image"):
        state["gen_urls"].append(url)
        yield state["gen_path"]

    @contextlib.asynccontextmanager
    async def res_path(url: str, *, scope_id: int, media_kind: str = "image"):
        state["resource_calls"].append((url, scope_id))
        yield state["resource"]

    monkeypatch.setattr(cms, "GeneratedMediaRepository", lambda: FakeGenRepo(state["rows"]))
    monkeypatch.setattr(cms, "_resolve_personal_team_id", personal_team)
    monkeypatch.setattr(
        cms, "get_conversation_repository", lambda: FakeMembership(state["teams"])
    )
    monkeypatch.setattr(cms, "generated_media_local_path", gen_path)
    monkeypatch.setattr(cms, "resource_local_path", res_path)
    return state


async def _load(url: str) -> cms.DeriveInput:
    return await cms.load_canvas_material(url, user_id="u1", canvas_scope_id=42)


async def test_generation_in_personal_scope_loads(wire: dict[str, Any]) -> None:
    got = await _load("/api/v1/generated-media/5/cover?v=2")
    assert got.mime_type == "image/png"
    assert got.label == "gen:5"
    assert got.file_bytes.startswith(b"\x89PNG")
    assert wire["gen_urls"] == ["/api/v1/generated-media/5/file"]


async def test_generation_in_team_scope_loads_for_member(wire: dict[str, Any]) -> None:
    wire["rows"][5]["scope_id"] = "99"
    wire["teams"].add(99)
    assert (await _load("/api/v1/generated-media/5/cover")).label == "gen:5"


async def test_unreadable_generation_is_404(wire: dict[str, Any]) -> None:
    wire["rows"][5]["scope_id"] = "99"
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/cover")
    assert caught.value.status_code == 404


async def test_missing_generation_is_404(wire: dict[str, Any]) -> None:
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/6/cover")
    assert caught.value.status_code == 404


async def test_video_generation_is_400(wire: dict[str, Any]) -> None:
    wire["rows"][5]["media_kind"] = "video"
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/stream")
    assert caught.value.status_code == 400


async def test_generation_file_unavailable_is_502(wire: dict[str, Any]) -> None:
    wire["gen_path"] = None
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/cover")
    assert caught.value.status_code == 502


async def test_resource_reference_is_checked_against_canvas_scope(
    wire: dict[str, Any],
) -> None:
    got = await _load("/api/v1/resources/9/cover?token=abc")
    assert got.label == "resource:9"
    assert wire["resource_calls"] == [("/api/v1/resources/9/file", 42)]


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("not_in_scope", 403),
        ("no_image_file", 400),
        ("materialize_failed", 502),
    ],
)
async def test_resource_refusals_are_typed(
    wire: dict[str, Any], reason: str, status: int
) -> None:
    wire["resource"] = ResourceRefResolution(reason=reason)
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/resources/9/cover")
    assert caught.value.status_code == status
    assert reason in caught.value.detail


@pytest.mark.parametrize(
    "url", ["https://cdn.example/x.png", "blob:https://app.nous.ink/1", "", "data:image/png;base64,AAAA"]
)
async def test_unsupported_shapes_are_422(wire: dict[str, Any], url: str) -> None:
    with pytest.raises(DeriveError) as caught:
        await _load(url)
    assert caught.value.status_code == 422


async def test_undecodable_bytes_are_400(
    wire: dict[str, Any], tmp_path: Path
) -> None:
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not an image")
    wire["gen_path"] = str(junk)
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/cover")
    assert caught.value.status_code == 400


async def test_oversized_source_is_413(
    wire: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cms, "MAX_SOURCE_BYTES", 10)
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/cover")
    assert caught.value.status_code == 413
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_canvas_material_source.py -v`
Expected: FAIL，`ImportError: cannot import name 'canvas_material_source'`。

- [ ] **Step 3: 实现**

`backend/app/services/canvas/canvas_material_source.py`：

```python
"""Turn an image reference on a canvas into bytes a derive can transform.

The canvas editors send the URL of the image being edited — never a resources
row id — so an editor works on whatever the canvas shows, wherever it came
from. Two durable shapes exist (``classify_reference_url``) and each carries
its own read rule:

* ``/generated-media/{id}/…`` — the URL is not scoped, so the caller must be
  able to read the row's scope (``can_read_generation_scope``, the rule promote
  uses). Unreadable answers 404, same as missing: a snowflake the caller cannot
  read must not be distinguishable from one that does not exist.
* ``/resources/{id}/cover|file`` — must sit in the CANVAS's scope, the same
  check a canvas generation applies to its references.

Everything else is 422. Every refusal is a typed ``DeriveError``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image

from app.repositories.conversation_repository import get_conversation_repository
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.services.canvas.derive_persistence import DeriveError
from app.services.library.generated_media_service import (
    classify_reference_url,
    generated_media_local_path,
    resource_local_path,
)
from app.services.library.generation_access import can_read_generation_scope
from app.services.library.resources_service import _resolve_personal_team_id

#: Upper bound on a source read into memory for one derive.
MAX_SOURCE_BYTES = 64 * 1024 * 1024

_RESOURCE_REASON_STATUS: dict[str, int] = {
    "unknown_shape": 422,
    "not_in_scope": 403,
    "no_image_file": 400,
    "materialize_failed": 502,
}


@dataclass(frozen=True)
class DeriveInput:
    file_bytes: bytes
    mime_type: str
    label: str


async def load_canvas_material(
    url: str, *, user_id: str, canvas_scope_id: int
) -> DeriveInput:
    kind, row_id = classify_reference_url(_reference_path(url))
    if kind == "genmedia" and row_id is not None:
        return await _load_generation(row_id, user_id=user_id)
    if kind == "resource" and row_id is not None:
        return await _load_resource(row_id, canvas_scope_id=canvas_scope_id)
    raise DeriveError(status_code=422, detail="unsupported image reference")


def _reference_path(url: str) -> str:
    """Drop query and fragment from a RELATIVE reference.

    ``classify_reference_url`` keeps only the path of an absolute own-host URL
    but passes a relative one through whole, and the resource pattern is
    ``$``-anchored — ``/resources/9/cover?token=…`` would classify as unknown.
    """
    text = str(url or "").strip()
    if text.startswith("/") and not text.startswith("//"):
        return urlsplit(text).path
    return text


async def _load_generation(gen_id: int, *, user_id: str) -> DeriveInput:
    gen = await GeneratedMediaRepository().get_by_id(gen_id)
    if gen is None or not await can_read_generation_scope(
        gen,
        user_id=user_id,
        personal_team_id=int(await _resolve_personal_team_id(user_id)),
        membership=get_conversation_repository(),
    ):
        raise DeriveError(status_code=404, detail="source image not found")
    if gen.get("media_kind") != "image":
        raise DeriveError(status_code=400, detail="source is not an image")
    async with generated_media_local_path(
        f"/api/v1/generated-media/{gen_id}/file", media_kind="image"
    ) as path:
        if path is None:
            raise DeriveError(status_code=502, detail="source image file unavailable")
        data = _read_capped(path)
    return DeriveInput(
        file_bytes=data, mime_type=sniff_image_mime(data), label=f"gen:{gen_id}"
    )


async def _load_resource(resource_id: int, *, canvas_scope_id: int) -> DeriveInput:
    async with resource_local_path(
        f"/api/v1/resources/{resource_id}/file",
        scope_id=canvas_scope_id,
        media_kind="image",
    ) as resolution:
        if resolution.path is None:
            reason = resolution.reason or "no_image_file"
            raise DeriveError(
                status_code=_RESOURCE_REASON_STATUS.get(reason, 400),
                detail=f"source image unavailable: {reason}",
            )
        data = _read_capped(resolution.path)
    return DeriveInput(
        file_bytes=data, mime_type=sniff_image_mime(data), label=f"resource:{resource_id}"
    )


def _read_capped(path: str) -> bytes:
    if os.path.getsize(path) > MAX_SOURCE_BYTES:
        raise DeriveError(status_code=413, detail="source image is too large")
    with open(path, "rb") as fh:
        return fh.read()


def sniff_image_mime(data: bytes) -> str:
    """The encoded format, from the bytes — never from a stored column."""
    try:
        with Image.open(BytesIO(data)) as img:
            fmt = img.format or ""
    except Exception as exc:
        raise DeriveError(
            status_code=400, detail="source is not a decodable image"
        ) from exc
    mime = Image.MIME.get(fmt)
    if not mime:
        raise DeriveError(status_code=400, detail="source is not a decodable image")
    return mime
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_canvas_material_source.py -v`
Expected: 全部 PASS。若 `import` 阶段出现循环导入（`generated_media_service` ↔ repository），把 `GeneratedMediaRepository` / `get_conversation_repository` / `_resolve_personal_team_id` 三个 import 保持在模块顶部不变、改为在 `_load_generation` 内部 import **不可行**（测试靠 `monkeypatch.setattr(cms, …)` 打桩）——应先 `uv run python -c "import app.services.canvas.canvas_material_source"` 找出环，再在环的另一端做懒导入。

- [ ] **Step 5: 格式化 + lint + 提交**

```bash
cd backend
uv run black app/services/canvas/canvas_material_source.py tests/test_canvas_material_source.py
uv run isort app/services/canvas/canvas_material_source.py tests/test_canvas_material_source.py
uv run ruff check app/services/canvas/canvas_material_source.py tests/test_canvas_material_source.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-backend add backend/app/services/canvas/canvas_material_source.py backend/tests/test_canvas_material_source.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-backend commit -m "feat(canvas): 画布素材加载器——按图片 URL 取字节并校验来源读权限"
```

### Task 5: 画布派生服务——加载 → 变换 → 注册进画布 scope

**Files:**
- Create: `backend/app/services/canvas/canvas_derive_service.py`
- Create: `backend/tests/test_canvas_derive_service.py`

**Interfaces:**
- Consumes: Task 1 的 `crop_image` / `split_image_by_lines` / `extend_image`；Task 3 的 `DERIVED`；Task 4 的 `DeriveInput` / `load_canvas_material` / `sniff_image_mime`；`register_generated_media(*, user_id, scope_id, source_path, mime, origin) -> dict`、`GenerationOrigin`（`generated_media_service.py`）；`canvas_generation._registration_scope_id(canvas_id, user_id) -> int`（解析不出时抛 `RuntimeError`）。
- Produces:
  - `canvas_derive_service.CanvasDerivedImage(id: str, url: str, row: int | None = None, col: int | None = None)`（frozen dataclass；`url` 恒为 `/api/v1/generated-media/{id}/cover`）
  - `derive_canvas_crop(*, canvas_id: int, user_id: str, source_url: str, region: CropRegion, node_id: str | None = None) -> CanvasDerivedImage`
  - `derive_canvas_grid(*, canvas_id: int, user_id: str, source_url: str, xs: Sequence[float], ys: Sequence[float], node_id: str | None = None) -> list[CanvasDerivedImage]`
  - `derive_canvas_outpaint(*, canvas_id: int, user_id: str, source_url: str, padding: Padding, prompt: str | None = None, mode: str = "deterministic", node_id: str | None = None) -> CanvasDerivedImage`
  - 注册的 `params`：`{"role": "derived", "op": <op>, "derived_from": <DeriveInput.label>, …op 参数}`。**`derived_from` 存 `gen:5` / `resource:9` 这种规范标签，绝不存调用方传来的 URL**——遗留 resource URL 可能带 `?token=eyJ…`，那会把会话令牌写进数据库。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_canvas_derive_service.py`：

```python
"""Canvas derive: load → transform → register into the canvas's scope."""

from __future__ import annotations

import os
import stat
from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from app.services.canvas import canvas_derive_service as cds
from app.services.canvas.canvas_material_source import DeriveInput
from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding


def _png(w: int = 10, h: int = 8) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"registered": [], "loads": [], "next_id": 900}

    async def scope_for_canvas(canvas_id: int, user_id: str) -> int:
        return 42

    async def load(url: str, *, user_id: str, canvas_scope_id: int) -> DeriveInput:
        state["loads"].append((url, user_id, canvas_scope_id))
        return DeriveInput(file_bytes=_png(), mime_type="image/png", label="gen:5")

    async def register(**kwargs: Any) -> dict[str, Any]:
        path = kwargs["source_path"]
        with Image.open(path) as img:
            size = img.size
        state["registered"].append(
            {
                **kwargs,
                "size": size,
                "dir_mode": stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode),
            }
        )
        state["next_id"] += 1
        return {"id": state["next_id"]}

    monkeypatch.setattr(cds, "_scope_for_canvas", scope_for_canvas)
    monkeypatch.setattr(cds, "load_canvas_material", load)
    monkeypatch.setattr(cds, "register_generated_media", register)
    return state


async def test_crop_registers_a_visible_derived_generation(
    wire: dict[str, Any],
) -> None:
    out = await cds.derive_canvas_crop(
        canvas_id=1,
        user_id="u1",
        source_url="/api/v1/resources/9/cover?token=eyJsecret",
        region=CropRegion(x=0.0, y=0.0, width=0.5, height=1.0),
        node_id="n1",
    )
    assert out == cds.CanvasDerivedImage(
        id="901", url="/api/v1/generated-media/901/cover"
    )
    assert wire["loads"] == [("/api/v1/resources/9/cover?token=eyJsecret", "u1", 42)]
    [reg] = wire["registered"]
    assert reg["user_id"] == "u1"
    assert reg["scope_id"] == 42
    assert reg["mime"] == "image/png"
    assert reg["size"] == (5, 8)
    assert reg["dir_mode"] == 0o700
    assert not os.path.exists(reg["source_path"])
    origin = reg["origin"]
    assert origin.kind == "canvas_upload"
    assert origin.canvas_id == 1
    assert origin.node_id == "n1"
    assert origin.derivation_kind == "crop"
    assert origin.params == {
        "role": "derived",
        "op": "crop",
        "derived_from": "gen:5",
        "region": {"x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0},
    }
    assert "eyJsecret" not in repr(origin)


async def test_grid_registers_one_generation_per_tile(wire: dict[str, Any]) -> None:
    out = await cds.derive_canvas_grid(
        canvas_id=1, user_id="u1", source_url="/api/v1/generated-media/5/cover",
        xs=[0.5], ys=[],
    )
    assert [(i.row, i.col, i.id) for i in out] == [(0, 0, "901"), (0, 1, "902")]
    assert [r["size"] for r in wire["registered"]] == [(5, 8), (5, 8)]
    assert wire["registered"][1]["origin"].params == {
        "role": "derived",
        "op": "grid",
        "derived_from": "gen:5",
        "xs": [0.5],
        "ys": [],
        "row": 0,
        "col": 1,
    }


async def test_outpaint_registers_extended_image_with_prompt(
    wire: dict[str, Any],
) -> None:
    out = await cds.derive_canvas_outpaint(
        canvas_id=1,
        user_id="u1",
        source_url="/api/v1/generated-media/5/cover",
        padding=Padding(left=0.5, top=0.0, right=0.5, bottom=0.0),
        prompt="a windswept meadow",
    )
    assert out.url == "/api/v1/generated-media/901/cover"
    [reg] = wire["registered"]
    assert reg["size"] == (20, 8)
    assert reg["origin"].prompt == "a windswept meadow"
    assert reg["origin"].params["padding"] == {
        "left": 0.5, "top": 0.0, "right": 0.5, "bottom": 0.0,
    }
    assert reg["origin"].params["mode"] == "deterministic"


async def test_source_refusal_propagates_before_any_registration(
    wire: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(url: str, *, user_id: str, canvas_scope_id: int) -> DeriveInput:
        raise DeriveError(status_code=404, detail="source image not found")

    monkeypatch.setattr(cds, "load_canvas_material", refuse)
    with pytest.raises(DeriveError) as caught:
        await cds.derive_canvas_crop(
            canvas_id=1, user_id="u1", source_url="/api/v1/generated-media/5/cover",
            region=CropRegion(x=0.0, y=0.0, width=0.5, height=0.5),
        )
    assert caught.value.status_code == 404
    assert wire["registered"] == []


async def test_invalid_region_fails_before_registration(wire: dict[str, Any]) -> None:
    with pytest.raises(DeriveError) as caught:
        await cds.derive_canvas_grid(
            canvas_id=1, user_id="u1", source_url="/api/v1/generated-media/5/cover",
            xs=[], ys=[],
        )
    assert caught.value.status_code == 400
    assert wire["registered"] == []


async def test_registration_without_id_is_500(
    wire: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_id(**kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(cds, "register_generated_media", no_id)
    with pytest.raises(DeriveError) as caught:
        await cds.derive_canvas_crop(
            canvas_id=1, user_id="u1", source_url="/api/v1/generated-media/5/cover",
            region=CropRegion(x=0.0, y=0.0, width=0.5, height=0.5),
        )
    assert caught.value.status_code == 500


async def test_unresolvable_canvas_scope_is_500(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.workflows.canvas_generation as cg

    async def unresolved(canvas_id: int | None, user_id: str | None) -> int:
        raise RuntimeError("scope_unresolved")

    monkeypatch.setattr(cg, "_registration_scope_id", unresolved)
    with pytest.raises(DeriveError) as caught:
        await cds._scope_for_canvas(1, "u1")
    assert caught.value.status_code == 500
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_canvas_derive_service.py -v`
Expected: FAIL，`ImportError: cannot import name 'canvas_derive_service'`。

- [ ] **Step 3: 实现**

`backend/app/services/canvas/canvas_derive_service.py`：

```python
"""Canvas derive: any image the canvas shows → crop / grid / outpaint → Tier-1.

    1. ``_scope_for_canvas`` — the scope canvas runs register into.
    2. ``load_canvas_material`` — URL → bytes, with the source's read rule.
    3. the pure transform (``crop_image`` / ``split_image_by_lines`` /
       ``extend_image``) — every tile is cropped before anything registers.
    4. ``register_generated_media`` — one generated_media row per product,
       role ``derived``, visible in the canvas scope's Generated inbox.

A grid whose registration fails mid-loop keeps the tiles already registered —
each is a valid generation on its own — and the caller sees the error.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any, Sequence

from app.services.canvas.canvas_material_source import (
    DeriveInput,
    load_canvas_material,
    sniff_image_mime,
)
from app.services.canvas.crop_derive_service import crop_image
from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.grid_derive_service import split_image_by_lines
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding
from app.services.canvas.outpaint_derive_service import extend_image
from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_generated_media,
)
from app.services.library.generated_roles import DERIVED, ROLE_KEY


@dataclass(frozen=True)
class CanvasDerivedImage:
    id: str
    url: str
    row: int | None = None
    col: int | None = None


@dataclass(frozen=True)
class _Target:
    canvas_id: int
    user_id: str
    node_id: str | None
    scope_id: int


async def _scope_for_canvas(canvas_id: int, user_id: str) -> int:
    """The canvas run registration scope. Lazy import: the workflow module is
    heavy and this service is imported at router load."""
    from app.workflows.canvas_generation import _registration_scope_id

    try:
        return await _registration_scope_id(canvas_id, user_id)
    except RuntimeError as exc:
        raise DeriveError(
            status_code=500, detail="canvas scope could not be resolved"
        ) from exc


async def _prepare(
    *, canvas_id: int, user_id: str, source_url: str, node_id: str | None
) -> tuple[_Target, DeriveInput]:
    scope_id = await _scope_for_canvas(canvas_id, user_id)
    source = await load_canvas_material(
        source_url, user_id=user_id, canvas_scope_id=scope_id
    )
    target = _Target(
        canvas_id=canvas_id, user_id=user_id, node_id=node_id, scope_id=scope_id
    )
    return target, source


async def _register(
    target: _Target,
    source: DeriveInput,
    image_bytes: bytes,
    *,
    op: str,
    op_params: dict[str, Any],
    prompt: str | None = None,
    row: int | None = None,
    col: int | None = None,
) -> CanvasDerivedImage:
    mime = sniff_image_mime(image_bytes)
    # Private 0700 dir + random name + exclusive create; register copies the
    # file before returning, so the directory can go when this block exits.
    with tempfile.TemporaryDirectory(prefix="canvas_derive_") as tmp_dir:
        fd, tmp_path = tempfile.mkstemp(dir=tmp_dir, suffix=".img")
        with os.fdopen(fd, "wb") as out:
            out.write(image_bytes)
        row_data = await register_generated_media(
            user_id=target.user_id,
            scope_id=target.scope_id,
            source_path=tmp_path,
            mime=mime,
            origin=GenerationOrigin(
                kind="canvas_upload",
                canvas_id=target.canvas_id,
                node_id=target.node_id,
                prompt=prompt,
                derivation_kind=op,
                params={
                    ROLE_KEY: DERIVED,
                    "op": op,
                    "derived_from": source.label,
                    **op_params,
                },
            ),
        )
    gen_id = row_data.get("id")
    if gen_id is None:
        raise DeriveError(status_code=500, detail="derived image registration failed")
    return CanvasDerivedImage(
        id=str(gen_id),
        url=f"/api/v1/generated-media/{gen_id}/cover",
        row=row,
        col=col,
    )


async def derive_canvas_crop(
    *,
    canvas_id: int,
    user_id: str,
    source_url: str,
    region: CropRegion,
    node_id: str | None = None,
) -> CanvasDerivedImage:
    target, source = await _prepare(
        canvas_id=canvas_id, user_id=user_id, source_url=source_url, node_id=node_id
    )
    image = crop_image(source.file_bytes, source.mime_type, region)
    return await _register(
        target,
        source,
        image,
        op="crop",
        op_params={
            "region": {
                "x": region.x,
                "y": region.y,
                "width": region.width,
                "height": region.height,
            }
        },
    )


async def derive_canvas_grid(
    *,
    canvas_id: int,
    user_id: str,
    source_url: str,
    xs: Sequence[float],
    ys: Sequence[float],
    node_id: str | None = None,
) -> list[CanvasDerivedImage]:
    target, source = await _prepare(
        canvas_id=canvas_id, user_id=user_id, source_url=source_url, node_id=node_id
    )
    tiles = split_image_by_lines(source.file_bytes, source.mime_type, xs=xs, ys=ys)
    results: list[CanvasDerivedImage] = []
    for tile in tiles:
        results.append(
            await _register(
                target,
                source,
                tile.image_bytes,
                op="grid",
                op_params={
                    "xs": list(xs),
                    "ys": list(ys),
                    "row": tile.row,
                    "col": tile.col,
                },
                row=tile.row,
                col=tile.col,
            )
        )
    return results


async def derive_canvas_outpaint(
    *,
    canvas_id: int,
    user_id: str,
    source_url: str,
    padding: Padding,
    prompt: str | None = None,
    mode: str = "deterministic",
    node_id: str | None = None,
) -> CanvasDerivedImage:
    target, source = await _prepare(
        canvas_id=canvas_id, user_id=user_id, source_url=source_url, node_id=node_id
    )
    image = await extend_image(
        source.file_bytes,
        source.mime_type,
        padding,
        prompt=prompt,
        mode=mode,
        label=source.label,
    )
    return await _register(
        target,
        source,
        image,
        op="outpaint",
        op_params={
            "padding": {
                "left": padding.left,
                "top": padding.top,
                "right": padding.right,
                "bottom": padding.bottom,
            },
            "mode": mode,
        },
        prompt=prompt,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_canvas_derive_service.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 格式化 + lint + 提交**

```bash
cd backend
uv run black app/services/canvas/canvas_derive_service.py tests/test_canvas_derive_service.py
uv run isort app/services/canvas/canvas_derive_service.py tests/test_canvas_derive_service.py
uv run ruff check app/services/canvas/canvas_derive_service.py tests/test_canvas_derive_service.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-backend add backend/app/services/canvas/canvas_derive_service.py backend/tests/test_canvas_derive_service.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-backend commit -m "feat(canvas): 画布派生服务——裁剪/切图/扩图产物注册为画布 scope 的生成内容"
```

### Task 6: 请求体 schema + 画布派生路由

**Files:**
- Create: `backend/app/schemas/canvas_material_derive_schema.py`
- Create: `backend/app/api/canvas_derive_router.py`
- Modify: `backend/app/api/__init__.py`（第 26 行 `canvases_router` import 之后加 import；第 148 行 `include_router(canvases_router…)` 之后加注册）
- Create: `backend/tests/test_canvas_derive_router.py`

**Interfaces:**
- Consumes: Task 5 的三个 `derive_canvas_*` 与 `CanvasDerivedImage`；`canvases_router._gate_canvas_write(canvas_id: str, auth) -> str`（`app/api/canvases_router.py:88`，无画布 404、无写权限 403）；`CropRegionModel`（`app/schemas/canvas_crop_schema.py`）；`MAX_LINES_PER_AXIS`、`MAX_PAD_PER_SIDE`。
- Produces（HTTP 契约，前端 Task 10 依赖）：
  - `POST /api/v1/canvases/{canvas_id}/derive-crop`，body `{"source_url": str, "node_id"?: str, "region": {"x","y","width","height"}}`
  - `POST /api/v1/canvases/{canvas_id}/derive-grid`，body `{"source_url", "node_id"?, "xs": number[], "ys": number[]}`
  - `POST /api/v1/canvases/{canvas_id}/derive-outpaint`，body `{"source_url", "node_id"?, "left","top","right","bottom", "mode"?: "deterministic"|"ai", "prompt"?: str}`
  - 成功（三者同形）：`{"success": true, "data": {"images": [{"id": "901", "url": "/api/v1/generated-media/901/cover", "kind": "image", "row": null, "col": null}]}}`；grid 的 `row`/`col` 为 0 起的整数。
  - 失败：`ErrorResponse` 外壳；4xx 的 `error` 是服务给的文案，5xx 的 `error` 被统一替换成 `"Internal server error"`（`app/core/exceptions.py`）。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_canvas_derive_router.py`：

```python
"""HTTP tests for the canvas derive endpoints. Hermetic: auth overridden,
canvas write gate and derive service stubbed."""

from __future__ import annotations

import sys
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.services.canvas import canvas_derive_service as cds
from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding

derive_router = sys.modules["app.api.canvas_derive_router"]

FAKE_USER_ID = str(uuid4())


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest.fixture(autouse=True)
def gate(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _fake_gate(canvas_id: str, auth: Any) -> str:
        calls.append(canvas_id)
        return "777"

    monkeypatch.setattr(derive_router, "_gate_canvas_write", _fake_gate)
    return calls


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _image(gen_id: str, row: int | None = None, col: int | None = None):
    return cds.CanvasDerivedImage(
        id=gen_id, url=f"/api/v1/generated-media/{gen_id}/cover", row=row, col=col
    )


async def test_crop_returns_the_derived_image(
    client: AsyncClient, gate: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_crop(**kwargs: Any) -> cds.CanvasDerivedImage:
        seen.update(kwargs)
        return _image("901")

    monkeypatch.setattr(cds, "derive_canvas_crop", fake_crop)
    resp = await client.post(
        "/api/v1/canvases/123/derive-crop",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "node_id": "n1",
            "region": {"x": 0, "y": 0, "width": 0.5, "height": 1},
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "data": {
            "images": [
                {
                    "id": "901",
                    "url": "/api/v1/generated-media/901/cover",
                    "kind": "image",
                    "row": None,
                    "col": None,
                }
            ]
        },
    }
    assert gate == ["123"]
    assert seen == {
        "canvas_id": 123,
        "user_id": FAKE_USER_ID,
        "source_url": "/api/v1/generated-media/5/cover",
        "node_id": "n1",
        "region": CropRegion(x=0.0, y=0.0, width=0.5, height=1.0),
    }


async def test_grid_returns_tiles_with_row_col(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_grid(**kwargs: Any) -> list[cds.CanvasDerivedImage]:
        assert kwargs["xs"] == [0.5] and kwargs["ys"] == []
        return [_image("901", 0, 0), _image("902", 0, 1)]

    monkeypatch.setattr(cds, "derive_canvas_grid", fake_grid)
    resp = await client.post(
        "/api/v1/canvases/123/derive-grid",
        json={"source_url": "/api/v1/generated-media/5/cover", "xs": [0.5], "ys": []},
    )
    assert resp.status_code == 200
    images = resp.json()["data"]["images"]
    assert [(i["id"], i["row"], i["col"]) for i in images] == [
        ("901", 0, 0),
        ("902", 0, 1),
    ]


async def test_outpaint_passes_padding_prompt_and_default_mode(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_outpaint(**kwargs: Any) -> cds.CanvasDerivedImage:
        seen.update(kwargs)
        return _image("901")

    monkeypatch.setattr(cds, "derive_canvas_outpaint", fake_outpaint)
    resp = await client.post(
        "/api/v1/canvases/123/derive-outpaint",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "left": 0.5,
            "right": 0.5,
            "prompt": "a windswept meadow",
        },
    )
    assert resp.status_code == 200
    assert seen["padding"] == Padding(left=0.5, top=0.0, right=0.5, bottom=0.0)
    assert seen["prompt"] == "a windswept meadow"
    assert seen["mode"] == "deterministic"


async def test_service_refusal_maps_to_typed_http_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(**kwargs: Any) -> cds.CanvasDerivedImage:
        raise DeriveError(status_code=404, detail="source image not found")

    monkeypatch.setattr(cds, "derive_canvas_crop", refuse)
    resp = await client.post(
        "/api/v1/canvases/123/derive-crop",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "region": {"x": 0, "y": 0, "width": 0.5, "height": 0.5},
        },
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == "http_404"
    assert body["error"] == "source image not found"


async def test_gate_refusal_stops_before_the_service(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def deny(canvas_id: str, auth: Any) -> str:
        raise HTTPException(status_code=403, detail="Access denied")

    async def must_not_run(**kwargs: Any) -> cds.CanvasDerivedImage:
        raise AssertionError("service ran past a refused gate")

    monkeypatch.setattr(derive_router, "_gate_canvas_write", deny)
    monkeypatch.setattr(cds, "derive_canvas_crop", must_not_run)
    resp = await client.post(
        "/api/v1/canvases/123/derive-crop",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "region": {"x": 0, "y": 0, "width": 0.5, "height": 0.5},
        },
    )
    assert resp.status_code == 403


async def test_unexpected_failure_is_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(**kwargs: Any) -> cds.CanvasDerivedImage:
        raise ValueError("disk on fire")

    monkeypatch.setattr(cds, "derive_canvas_crop", boom)
    resp = await client.post(
        "/api/v1/canvases/123/derive-crop",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "region": {"x": 0, "y": 0, "width": 0.5, "height": 0.5},
        },
    )
    assert resp.status_code == 500
    assert "disk on fire" not in resp.text


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/canvases/abc/derive-crop", {"source_url": "/x", "region": {"x": 0, "y": 0, "width": 1, "height": 1}}),
        ("/api/v1/canvases/123/derive-crop", {"source_url": "", "region": {"x": 0, "y": 0, "width": 1, "height": 1}}),
        ("/api/v1/canvases/123/derive-crop", {"source_url": "/x", "region": {"x": 0, "y": 0, "width": 0, "height": 1}}),
        ("/api/v1/canvases/123/derive-grid", {"source_url": "/x", "xs": [0.1] * 40, "ys": []}),
        ("/api/v1/canvases/123/derive-outpaint", {"source_url": "/x", "left": -1}),
    ],
)
async def test_invalid_requests_are_422(
    client: AsyncClient, path: str, body: dict[str, Any]
) -> None:
    resp = await client.post(path, json=body)
    assert resp.status_code == 422
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_canvas_derive_router.py -v`
Expected: FAIL，`KeyError: 'app.api.canvas_derive_router'`。

- [ ] **Step 3: 实现 schema**

`backend/app/schemas/canvas_material_derive_schema.py`：

```python
"""Request bodies for the canvas-scoped derive endpoints.

Geometry validation (region bounds beyond the field ranges, line ordering,
padding caps) stays in the pure primitives and surfaces as a 400 — the schema
only bounds payload shape and size, same split as the resource derive schemas.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.canvas_crop_schema import CropRegionModel
from app.services.canvas.grid_split import MAX_LINES_PER_AXIS
from app.services.canvas.image_outpaint import MAX_PAD_PER_SIDE


class _CanvasDeriveBase(BaseModel):
    # The URL of the image being edited, exactly as the canvas holds it.
    source_url: str = Field(..., min_length=1, max_length=2048)
    # The node the edit was made from — provenance on the registered row.
    node_id: str | None = Field(default=None, max_length=128)


class CanvasCropDeriveRequest(_CanvasDeriveBase):
    region: CropRegionModel


class CanvasGridDeriveRequest(_CanvasDeriveBase):
    xs: list[float] = Field(default_factory=list, max_length=MAX_LINES_PER_AXIS)
    ys: list[float] = Field(default_factory=list, max_length=MAX_LINES_PER_AXIS)


class CanvasOutpaintDeriveRequest(_CanvasDeriveBase):
    left: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    top: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    right: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    bottom: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    mode: Literal["deterministic", "ai"] = Field(default="deterministic")
    prompt: str | None = Field(default=None, max_length=2000)
```

（`grid_split.MAX_LINES_PER_AXIS = 5`，测试里 40 条线必然触发 422。）

- [ ] **Step 4: 实现路由**

`backend/app/api/canvas_derive_router.py`：

```python
"""Canvas-scoped image derive — crop / grid / outpaint any image the canvas shows.

  POST /api/v1/canvases/{canvas_id}/derive-crop
  POST /api/v1/canvases/{canvas_id}/derive-grid
  POST /api/v1/canvases/{canvas_id}/derive-outpaint

Write access is checked on the CANVAS (``_gate_canvas_write``); read access on
the SOURCE image (``canvas_material_source``). Products are generated_media rows
in the canvas's own scope. Replaces the ``/resources/{id}/derive-*`` family for
the canvas editors, which required every image to be promoted into the library
first.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Sequence

from fastapi import APIRouter, Depends, HTTPException, Path
from loguru import logger

from app.api.canvases_router import _gate_canvas_write
from app.core.deps import AuthDep
from app.db.scope import Scope, request_scope
from app.schemas.canvas_material_derive_schema import (
    CanvasCropDeriveRequest,
    CanvasGridDeriveRequest,
    CanvasOutpaintDeriveRequest,
)
from app.services.canvas import canvas_derive_service as derive
from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding
from app.services.modules.gate import require_module

router = APIRouter(dependencies=[Depends(require_module("projects"))])

_CANVAS_ID_PATTERN = r"^\d{1,20}$"


def _ok(images: Sequence[derive.CanvasDerivedImage]) -> dict:
    return {
        "success": True,
        "data": {
            "images": [
                {
                    "id": image.id,
                    "url": image.url,
                    "kind": "image",
                    "row": image.row,
                    "col": image.col,
                }
                for image in images
            ]
        },
    }


async def _run(
    canvas_id: str,
    auth: AuthDep,
    op: str,
    work: Callable[[], Awaitable[list[derive.CanvasDerivedImage]]],
) -> dict:
    await _gate_canvas_write(canvas_id, auth)
    # Reading a resource source touches the scoped ``resources`` model, which
    # requires an ambient Scope at the request boundary.
    async with request_scope(Scope(user_id=str(auth.user_id))):
        try:
            return _ok(await work())
        except DeriveError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"canvas {canvas_id} derive-{op} failed: {exc!r}")
            raise HTTPException(
                status_code=500, detail=f"Failed to derive {op}"
            ) from exc


@router.post("/canvases/{canvas_id}/derive-crop")
async def derive_crop_endpoint(
    body: CanvasCropDeriveRequest,
    auth: AuthDep,
    canvas_id: str = Path(..., pattern=_CANVAS_ID_PATTERN),
) -> dict:
    async def work() -> list[derive.CanvasDerivedImage]:
        image = await derive.derive_canvas_crop(
            canvas_id=int(canvas_id),
            user_id=str(auth.user_id),
            source_url=body.source_url,
            node_id=body.node_id,
            region=CropRegion(
                x=body.region.x,
                y=body.region.y,
                width=body.region.width,
                height=body.region.height,
            ),
        )
        return [image]

    return await _run(canvas_id, auth, "crop", work)


@router.post("/canvases/{canvas_id}/derive-grid")
async def derive_grid_endpoint(
    body: CanvasGridDeriveRequest,
    auth: AuthDep,
    canvas_id: str = Path(..., pattern=_CANVAS_ID_PATTERN),
) -> dict:
    async def work() -> list[derive.CanvasDerivedImage]:
        return await derive.derive_canvas_grid(
            canvas_id=int(canvas_id),
            user_id=str(auth.user_id),
            source_url=body.source_url,
            node_id=body.node_id,
            xs=body.xs,
            ys=body.ys,
        )

    return await _run(canvas_id, auth, "grid", work)


@router.post("/canvases/{canvas_id}/derive-outpaint")
async def derive_outpaint_endpoint(
    body: CanvasOutpaintDeriveRequest,
    auth: AuthDep,
    canvas_id: str = Path(..., pattern=_CANVAS_ID_PATTERN),
) -> dict:
    async def work() -> list[derive.CanvasDerivedImage]:
        image = await derive.derive_canvas_outpaint(
            canvas_id=int(canvas_id),
            user_id=str(auth.user_id),
            source_url=body.source_url,
            node_id=body.node_id,
            padding=Padding(
                left=body.left, top=body.top, right=body.right, bottom=body.bottom
            ),
            prompt=body.prompt,
            mode=body.mode,
        )
        return [image]

    return await _run(canvas_id, auth, "outpaint", work)
```

`backend/app/api/__init__.py`：在 `from app.api.canvases_router import router as canvases_router` 下一行加

```python
from app.api.canvas_derive_router import router as canvas_derive_router
```

在 `api_router.include_router(router=canvases_router, tags=["Canvas"])` 下一行加

```python
api_router.include_router(router=canvas_derive_router, tags=["Canvas"])
```

- [ ] **Step 5: 跑测试确认通过 + 旧路由不受影响**

Run: `cd backend && uv run pytest tests/test_canvas_derive_router.py tests/test_canvas_generations_route.py -v`
Expected: 全部 PASS。再 `uv run python -c "from app.main import app; print(sorted(r.path for r in app.routes if 'derive' in r.path))"`，输出里同时有三个新路径与四个旧 `/resources/{resource_id}/derive-*`。

- [ ] **Step 6: 格式化 + lint + 提交**

```bash
cd backend
F="app/schemas/canvas_material_derive_schema.py app/api/canvas_derive_router.py app/api/__init__.py tests/test_canvas_derive_router.py"
uv run black $F && uv run isort $F && uv run ruff check $F
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-backend add backend/app/schemas/canvas_material_derive_schema.py backend/app/api/canvas_derive_router.py backend/app/api/__init__.py backend/tests/test_canvas_derive_router.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-backend commit -m "feat(canvas): POST /canvases/{id}/derive-crop|grid|outpaint——按图片 URL 派生"
```

- [ ] **Step 7: PR2 全量回归 + 开 PR**

```bash
cd backend && uv run pytest -q -x --ignore=tests/api --deselect tests/test_storage_migration_web_resource_files.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-backend push -u origin feat/canvas-derive-any-reference-backend
gh pr create --repo iocrazy/nous-app --base master --head feat/canvas-derive-any-reference-backend \
  --title "feat(canvas): 画布派生端点接受任意图片引用，产物进画布 scope 的生成内容" \
  --body "计划：docs/superpowers/plans/2026-09-10-canvas-derive-any-reference.md（Task 3–6）。新增三端点，旧 /resources/{id}/derive-* 保留到 PR5。前端尚未切换，本 PR 对线上行为无影响。"
```

（`--ignore=tests/api` 只为绕开本机 distribution 假红；若 `tests/api` 下有本 PR 相关测试，单独点名跑。）CI 绿即合并，等 `deploy-gpu.yml` 完成后探 `readyz`。

---

## PR3 — `fix/canvas-refs-follow-archive`

前置：PR2 已合并（Task 8/9 依赖 PR1 的 `generation_access`，且与 PR2 同在 canvas / generated-media 周边，按顺序合并避免冲突）。worktree：`git -C /Volumes/program/project-code/repos/nous-app worktree add /Volumes/program/project-code/repos/nous-app-wt-derive-refs -b fix/canvas-refs-follow-archive origin/master`，`cd …/backend && uv sync`。

### Task 7: `canvas_resource_refs` 跟着归档走

现状缺陷：`canvas_resource_refs` 在每次画布保存时由 `extract_asset_refs(nodes_json)` 全量重建（`replace_for_canvas` 先删后插），而输出节点只有被自动 promote 过才有 `data.resource_id`。PR4 删掉自动 promote 后，用户在生成内容里手动"入库"的图，其画布引用（资源详情页的 "Used in canvases"、项目素材计数）会一直是空的。

改法：保存时额外取出输出节点正在显示的 generation id（`preview_url` 与 `images[].url`），查它们的 `promoted_resource_id`，已归档的补一条 `role='output'` 引用。表主键是 `(canvas_id, resource_id, node_id)`，合并时按 `(node_id, resource_id)` 去重。

**Files:**
- Modify: `backend/app/services/canvas/asset_refs.py`
- Create: `backend/tests/test_asset_refs_archived_outputs.py`
- Modify: `backend/app/repositories/generated_media_repository.py`
- Create: `backend/tests/repositories/test_generated_media_promoted_ids.py`
- Modify: `backend/app/services/canvas/canvas_service.py:37-45`（`__init__`）与 `:166-187`（`_sync_refs` 第一个 try 块）
- Modify: `backend/tests/test_canvas_refs_wiring.py`（文件末尾追加）

**Interfaces:**
- Consumes: `GENERATED_MEDIA_URL_RE`（`generated_media_service.py:519`，仅在契约测试里 import）；`read_scope()`、`GeneratedMedia` ORM 模型。
- Produces:
  - `asset_refs.extract_output_generation_ids(nodes_json: Any) -> list[tuple[str, int]]`
  - `asset_refs.merge_promoted_output_refs(refs: list[dict[str, str]], pairs: Iterable[tuple[str, int]], promoted: Mapping[int, int]) -> list[dict[str, str]]`（返回新列表，不改入参）
  - `generated_media_repository._promoted_resource_ids_stmt(gen_ids: Sequence[int])`
  - `GeneratedMediaRepository.promoted_resource_ids(gen_ids: Iterable[int | str]) -> dict[int, int]`（空输入不碰数据库）
  - `CanvasService.__init__(…, generated_media_repository: GeneratedMediaRepository | None = None)`

- [ ] **Step 1: 写抽取 / 合并的失败测试**

`backend/tests/test_asset_refs_archived_outputs.py`：

```python
"""Output nodes reference the archived copy of the generation they show."""

from __future__ import annotations

from app.services.canvas.asset_refs import (
    _GENERATED_MEDIA_URL_RE,
    extract_output_generation_ids,
    merge_promoted_output_refs,
)
from app.services.library.generated_media_service import GENERATED_MEDIA_URL_RE


def test_local_pattern_is_the_canonical_one() -> None:
    # asset_refs stays import-free on the save hot path; this pins the copy.
    assert _GENERATED_MEDIA_URL_RE.pattern == GENERATED_MEDIA_URL_RE.pattern


def test_extracts_generations_shown_by_output_nodes_only() -> None:
    nodes = [
        {
            "id": "out-1",
            "type": "output",
            "data": {
                "preview_url": "/api/v1/generated-media/5/cover?v=2",
                "images": [
                    {"url": "/api/v1/generated-media/5/cover"},
                    {"url": "/api/v1/generated-media/6/file"},
                    {"url": "blob:https://app.nous.ink/1"},
                    "junk",
                ],
            },
        },
        {"id": "media-1", "type": "media", "data": {"preview_url": "/api/v1/generated-media/7/cover"}},
        {"id": "out-2", "type": "output", "data": {"preview_url": "https://app.nous.ink/api/v1/generated-media/8/cover"}},
        {"id": "out-3", "type": "output", "data": None},
        "junk",
    ]
    assert extract_output_generation_ids(nodes) == [
        ("out-1", 5),
        ("out-1", 6),
        ("out-2", 8),
    ]


def test_non_list_nodes_yield_nothing() -> None:
    assert extract_output_generation_ids(None) == []
    assert extract_output_generation_ids({"id": "x"}) == []


def test_merge_adds_archived_outputs_once_and_leaves_input_alone() -> None:
    refs = [{"resource_id": "222", "role": "output", "node_id": "out-1"}]
    merged = merge_promoted_output_refs(
        refs,
        [("out-1", 5), ("out-1", 6), ("out-2", 7), ("out-2", 7)],
        {5: 222, 7: 333},
    )
    assert merged == [
        {"resource_id": "222", "role": "output", "node_id": "out-1"},
        {"resource_id": "333", "role": "output", "node_id": "out-2"},
    ]
    assert refs == [{"resource_id": "222", "role": "output", "node_id": "out-1"}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_asset_refs_archived_outputs.py -v`
Expected: FAIL，`ImportError: cannot import name '_GENERATED_MEDIA_URL_RE'`。

- [ ] **Step 3: 实现抽取 / 合并**

`asset_refs.py`：模块 docstring 的节点形状列表末尾补一行

```
  - output node → generated-media images it shows, once archived (role 'output')
```

import 区改为

```python
import re
from typing import Any, Dict, Iterable, List, Mapping, Tuple
```

在 `extract_asset_refs` 之前加：

```python
# Same pattern as generated_media_service.GENERATED_MEDIA_URL_RE — copied so
# this module stays import-free on the save hot path; a contract test pins
# the two together.
_GENERATED_MEDIA_URL_RE = re.compile(
    r"/generated-media/(\d+)/(?:cover|stream|file)(?=$|[?#])"
)
```

在 `_add` 之前加：

```python
def extract_output_generation_ids(nodes_json: Any) -> List[Tuple[str, int]]:
    """``(node_id, generation id)`` for every generated-media image an OUTPUT
    node shows (``preview_url`` and ``images[].url``), deduped, in order.

    The caller asks which of these have been archived into ``resources``; an
    archived one is as much this canvas's output as a legacy ``resource_id``.
    """
    if not isinstance(nodes_json, list):
        return []
    seen: set[Tuple[str, int]] = set()
    out: List[Tuple[str, int]] = []
    for idx, node in enumerate(nodes_json):
        if not isinstance(node, dict) or str(node.get("type") or "") != "output":
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        node_id = str(node.get("id") or f"node_{idx}")
        urls: List[Any] = [data.get("preview_url")]
        images = data.get("images")
        if isinstance(images, list):
            urls.extend(img.get("url") for img in images if isinstance(img, dict))
        for url in urls:
            if not isinstance(url, str):
                continue
            match = _GENERATED_MEDIA_URL_RE.search(url)
            if not match:
                continue
            key = (node_id, int(match.group(1)))
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def merge_promoted_output_refs(
    refs: List[Dict[str, str]],
    pairs: Iterable[Tuple[str, int]],
    promoted: Mapping[int, int],
) -> List[Dict[str, str]]:
    """``refs`` plus one ``output`` ref per shown generation that has a
    ``resources`` copy. Returns a new list; deduped on (node_id, resource_id),
    which is the table's key within one canvas."""
    out = list(refs)
    seen = {(r["node_id"], r["resource_id"]) for r in refs}
    for node_id, gen_id in pairs:
        resource_id = promoted.get(gen_id)
        if resource_id is None:
            continue
        key = (node_id, str(resource_id))
        if key in seen:
            continue
        seen.add(key)
        out.append({"resource_id": str(resource_id), "role": "output", "node_id": node_id})
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_asset_refs_archived_outputs.py tests/test_asset_refs.py -v`
Expected: PASS。

- [ ] **Step 5: 写仓储查询的失败测试**

`backend/tests/repositories/test_generated_media_promoted_ids.py`：

```python
"""generation id → promoted resource id, for the canvas refs mirror."""

from __future__ import annotations

import contextlib

from sqlalchemy.dialects import postgresql

from app.repositories import generated_media_repository as gmr


def test_statement_reads_only_promoted_rows_for_the_ids() -> None:
    sql = str(
        gmr._promoted_resource_ids_stmt([5, 6]).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "generated_media.id IN (5, 6)" in sql
    assert "generated_media.promoted_resource_id IS NOT NULL" in sql


async def test_no_ids_never_touch_the_database(monkeypatch) -> None:
    def refuse():
        raise AssertionError("queried with no ids")

    monkeypatch.setattr(gmr, "read_scope", refuse)
    assert await gmr.GeneratedMediaRepository().promoted_resource_ids([]) == {}


async def test_rows_become_an_int_map_over_deduped_ids(monkeypatch) -> None:
    executed: list = []

    class _Result:
        def all(self):
            return [(5, 222), (7, 333)]

    class _Session:
        async def execute(self, stmt):
            executed.append(stmt)
            return _Result()

    @contextlib.asynccontextmanager
    async def fake_read_scope():
        yield _Session()

    monkeypatch.setattr(gmr, "read_scope", fake_read_scope)
    got = await gmr.GeneratedMediaRepository().promoted_resource_ids(["5", 7, 5])
    assert got == {5: 222, 7: 333}
    [stmt] = executed
    sql = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "IN (5, 7)" in sql
```

- [ ] **Step 6: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/repositories/test_generated_media_promoted_ids.py -v`
Expected: FAIL，`AttributeError: … has no attribute '_promoted_resource_ids_stmt'`。

- [ ] **Step 7: 实现仓储查询**

`generated_media_repository.py`：import 区 `from typing import Optional` 改为

```python
from typing import Iterable, Optional, Sequence
```

在 `class GeneratedMediaRepository` 定义之前加：

```python
def _promoted_resource_ids_stmt(gen_ids: Sequence[int]):
    return select(GeneratedMedia.id, GeneratedMedia.promoted_resource_id).where(
        GeneratedMedia.id.in_(list(gen_ids)),
        GeneratedMedia.promoted_resource_id.is_not(None),
    )
```

在 `get_by_id` 方法之后加：

```python
    async def promoted_resource_ids(
        self, gen_ids: Iterable[int | str]
    ) -> dict[int, int]:
        """``{generation id: promoted resource id}`` for the archived ones.

        Unscoped like ``get_by_id``: the ids come from one canvas's own
        nodes_json and the answer only feeds that canvas's refs mirror.
        """
        ids = sorted({int(g) for g in gen_ids})
        if not ids:
            return {}
        async with read_scope() as session:
            rows = (await session.execute(_promoted_resource_ids_stmt(ids))).all()
        return {int(gen_id): int(resource_id) for gen_id, resource_id in rows}
```

Run: `cd backend && uv run pytest tests/repositories/test_generated_media_promoted_ids.py tests/repositories/test_generated_media_inbox_repo.py -v`
Expected: PASS。

- [ ] **Step 8: 写保存接线的失败测试**

`backend/tests/test_canvas_refs_wiring.py` 末尾追加：

```python
class FakeGenRepo:
    def __init__(self, promoted: Dict[int, int] | None = None, boom: bool = False):
        self.promoted = promoted or {}
        self.boom = boom
        self.calls: List[List[int]] = []

    async def promoted_resource_ids(self, gen_ids) -> Dict[int, int]:
        ids = [int(g) for g in gen_ids]
        self.calls.append(ids)
        if self.boom:
            raise RuntimeError("db down")
        return {g: r for g, r in self.promoted.items() if g in ids}


def _archived_update() -> CanvasUpdate:
    return CanvasUpdate(
        base_updated_at=FROZEN,
        nodes_json=[
            {
                "id": "out-1",
                "type": "output",
                "data": {
                    "kind": "image",
                    "preview_url": "/api/v1/generated-media/5/cover",
                    "images": [{"url": "/api/v1/generated-media/6/cover"}],
                },
            },
            {
                "id": "out-2",
                "type": "output",
                "data": {"kind": "image", "resource_id": "222"},
            },
        ],
    )


@pytest.mark.asyncio
async def test_save_counts_archived_generations_shown_by_output_nodes():
    refs_repo = FakeRefsRepo()
    gen_repo = FakeGenRepo({5: 333})
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=refs_repo,
        generated_media_repository=gen_repo,
    )
    await svc.update_with_lock("5001", _archived_update())
    assert gen_repo.calls == [[5, 6]]
    _, refs = refs_repo.calls[0]
    assert {(r["node_id"], r["resource_id"], r["role"]) for r in refs} == {
        ("out-1", "333", "output"),
        ("out-2", "222", "output"),
    }


@pytest.mark.asyncio
async def test_save_without_generation_urls_skips_the_lookup():
    refs_repo = FakeRefsRepo()
    gen_repo = FakeGenRepo({5: 333})
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=refs_repo,
        generated_media_repository=gen_repo,
    )
    upd = CanvasUpdate(
        base_updated_at=FROZEN,
        nodes_json=[{"id": "out-2", "type": "output", "data": {"resource_id": "222"}}],
    )
    await svc.update_with_lock("5001", upd)
    assert gen_repo.calls == []
    assert [r["resource_id"] for r in refs_repo.calls[0][1]] == ["222"]


@pytest.mark.asyncio
async def test_archived_lookup_failure_still_writes_the_legacy_refs():
    refs_repo = FakeRefsRepo()
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=refs_repo,
        generated_media_repository=FakeGenRepo(boom=True),
    )
    await svc.update_with_lock("5001", _archived_update())
    _, refs = refs_repo.calls[0]
    assert [(r["node_id"], r["resource_id"]) for r in refs] == [("out-2", "222")]
```

Run: `cd backend && uv run pytest tests/test_canvas_refs_wiring.py -v`
Expected: 三个新测试 FAIL（`TypeError: … unexpected keyword argument 'generated_media_repository'`），旧测试 PASS。

- [ ] **Step 9: 实现保存接线**

`canvas_service.py`：import 区加

```python
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.services.canvas.asset_refs import (
    extract_asset_refs,
    extract_output_generation_ids,
    merge_promoted_output_refs,
)
```

（替换原来单独 import `extract_asset_refs` 的那一行。）

`__init__` 增加参数与字段：

```python
    def __init__(
        self,
        repository: Optional[CanvasRepository] = None,
        refs_repository: Optional[CanvasRefsRepository] = None,
        asset_refs_repository: Optional[CanvasAssetRefsRepository] = None,
        generated_media_repository: Optional[GeneratedMediaRepository] = None,
    ) -> None:
        self.repo = repository or CanvasRepository()
        self.refs_repo = refs_repository or CanvasRefsRepository()
        self.asset_refs_repo = asset_refs_repository or CanvasAssetRefsRepository()
        self.gen_repo = generated_media_repository or GeneratedMediaRepository()
```

`_sync_refs` 第一个 try 块改为：

```python
        try:
            refs = extract_asset_refs(nodes_json)
            refs = await self._with_archived_outputs(canvas_id, nodes_json, refs)
            await self.refs_repo.replace_for_canvas(canvas_id, refs)
        except Exception as e:  # noqa: BLE001 — contained, logged, non-fatal
            logger.error(
                f"canvas {canvas_id} resource-refs sync failed (non-fatal): {e}"
            )
```

在 `_sync_refs` 之后加：

```python
    async def _with_archived_outputs(
        self, canvas_id: str, nodes_json: Any, refs: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """``refs`` plus the output refs whose generation has been archived.

        Its own try: a failed lookup degrades to the legacy refs and says so —
        it must not stop them being written.
        """
        pairs = extract_output_generation_ids(nodes_json)
        if not pairs:
            return refs
        try:
            promoted = await self.gen_repo.promoted_resource_ids(
                gen_id for _, gen_id in pairs
            )
        except Exception as e:  # noqa: BLE001 — contained, logged, non-fatal
            logger.error(
                f"canvas {canvas_id} archived-output ref lookup failed "
                f"(non-fatal, legacy refs only): {e}"
            )
            return refs
        return merge_promoted_output_refs(refs, pairs, promoted)
```

- [ ] **Step 10: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_canvas_refs_wiring.py tests/test_asset_refs.py tests/test_asset_refs_archived_outputs.py tests/repositories/test_generated_media_promoted_ids.py -v`
Expected: 全部 PASS。

- [ ] **Step 11: 格式化 + lint + 提交**

```bash
cd backend
F="app/services/canvas/asset_refs.py app/repositories/generated_media_repository.py app/services/canvas/canvas_service.py tests/test_asset_refs_archived_outputs.py tests/repositories/test_generated_media_promoted_ids.py tests/test_canvas_refs_wiring.py"
uv run black $F && uv run isort $F && uv run ruff check $F
W=/Volumes/program/project-code/repos/nous-app-wt-derive-refs
git -C $W add $(for f in $F; do echo backend/$f; done)
git -C $W commit -m "fix(canvas): 画布资源引用跟着归档走——输出节点显示的已入库生成图计入 output 引用"
```

### Task 8: 放大——源必须可读，结果进源 generation 自己的 scope

现状缺陷（两个）：`upscale_generation` 对 `gen_id` **没有任何读权限校验**（任何登录用户都能放大任意 id 并拿到一份副本）；结果注册进**调用者个人空间**，团队画板上放大的图跑进个人收件箱（与 PR #2212 修过的 promote 同一类）。

**Files:**
- Modify: `backend/app/api/generated_media_router.py`（`_upscale_provider` 附近加 `_membership` seam；`upscale_generation` 函数体）
- Modify: `backend/tests/test_generated_media_upscale_route.py`

**Interfaces:**
- Consumes: Task 2 的 `can_read_generation_scope`；`GeneratedMediaRepository().get_by_id`；`_scope(auth) -> int`（调用者个人 team）。
- Produces: `generated_media_router._membership()`（seam，返回 `get_conversation_repository()`）；HTTP 行为：源不存在或不可读 → 404 `"generation not found"`，且不调用放大 provider；成功时 `_register_upscale_result(scope_id=<源行 scope_id>)`。

- [ ] **Step 1: 改测试（先红）**

`test_generated_media_upscale_route.py`：在 `client` fixture 之后加共享桩：

```python
class _FakeGenRepo:
    def __init__(self, rows):
        self.rows = rows

    async def get_by_id(self, gen_id):
        return self.rows.get(int(gen_id))


class _FakeMembership:
    def __init__(self, teams):
        self.teams = teams

    async def is_team_member(self, *, team_id, user_id):
        return team_id in self.teams


def _patch_source(monkeypatch, *, rows, teams=()):
    async def _fake_scope(auth):
        return 42

    monkeypatch.setattr(r, "_scope", _fake_scope)
    monkeypatch.setattr(r, "GeneratedMediaRepository", lambda: _FakeGenRepo(rows))
    monkeypatch.setattr(r, "_membership", lambda: _FakeMembership(set(teams)))
```

`test_upscale_route_runs_cli_and_registers_result` 里删掉 `_fake_scope` 定义与 `monkeypatch.setattr(r, "_scope", _fake_scope)`，改为在其它 `setattr` 之前调用

```python
    _patch_source(
        monkeypatch,
        rows={7: {"id": "7", "scope_id": "99", "media_kind": "image"}},
        teams={99},
    )
```

并在末尾追加断言

```python
    assert seen["register"]["scope_id"] == 99
```

文件末尾追加：

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        {},
        {7: {"id": "7", "scope_id": "99", "media_kind": "image"}},
    ],
    ids=["missing", "not-a-member"],
)
async def test_upscale_refuses_a_source_the_caller_cannot_read(
    monkeypatch, client, rows
):
    class _MustNotRun:
        async def upscale_image(self, **kwargs):
            raise AssertionError("provider ran for an unreadable source")

    _patch_source(monkeypatch, rows=rows, teams=())
    monkeypatch.setattr(r, "_upscale_provider", lambda: _MustNotRun())

    resp = await client.post(
        "/api/v1/generated-media/7/upscale", json={"resolution": "2k"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "generation not found"
```

Run: `cd backend && uv run pytest tests/test_generated_media_upscale_route.py -v`
Expected: FAIL——`AttributeError: … has no attribute '_membership'`（`monkeypatch.setattr` 默认 `raising=True`）。

- [ ] **Step 2: 实现**

`generated_media_router.py` 顶部 import 区加：

```python
from app.services.library.generation_access import can_read_generation_scope
```

在 `_upscale_provider` 之前加：

```python
def _membership():
    """Seam: the team-membership lookup (patched in tests)."""
    from app.repositories.conversation_repository import get_conversation_repository

    return get_conversation_repository()
```

`upscale_generation` 的 docstring 之后、`async with request_scope(...)` 之前，把 `scope_id = await _scope(auth)` 替换为：

```python
    # Read gate on the SOURCE, then file the result where the source lives —
    # the caller's personal team is the wrong home for a team board's image
    # (the promote fix in #2212 settled the same question).
    source = await GeneratedMediaRepository().get_by_id(gen_id)
    if source is None or not await can_read_generation_scope(
        source,
        user_id=str(auth.user_id),
        personal_team_id=await _scope(auth),
        membership=_membership(),
    ):
        raise HTTPException(status_code=404, detail="generation not found")
    scope_id = int(source["scope_id"])
```

（其余函数体不变，`_register_upscale_result(scope_id=scope_id, …)` 自然用上源 scope。`can_read_generation_scope` 对 `scope_id is None` 返回 False，所以 `int(source["scope_id"])` 不会遇到 None。）

- [ ] **Step 3: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_generated_media_upscale_route.py tests/test_generated_media_promote_route.py -v`
Expected: 全部 PASS。

- [ ] **Step 4: 格式化 + lint + 提交**

```bash
cd backend
F="app/api/generated_media_router.py tests/test_generated_media_upscale_route.py"
uv run black $F && uv run isort $F && uv run ruff check $F
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refs add backend/app/api/generated_media_router.py backend/tests/test_generated_media_upscale_route.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refs commit -m "fix(generated): 放大先校验源图读权限，结果落在源 generation 自己的 scope"
```

### Task 9: 画布内导入进画布 scope（D9）

现状：`POST /generated-media/import` 带 `canvas_id` 时既不校验画布写权限，也把遮罩 / 画笔 / 缩放产物注册进调用者个人空间。改为：带 `canvas_id` → 过 `_gate_canvas_write` → 注册进 `_registration_scope_id`，与 Task 5 的派生产物同一个 scope。不带 `canvas_id` 行为不变。解析位置保持在"读完文件之后、注册之前"（`test_import_rejects_oversize_and_bad_canvas_id` 依赖超限在解析 scope 之前抛出）。

**Files:**
- Modify: `backend/app/api/generated_media_router.py`（`_scope` 之后加两个函数；`import_generation` 中 `scope_id=await _scope(auth)` 一处）
- Modify: `backend/tests/test_generated_media_import.py`

**Interfaces:**
- Consumes: `canvases_router._gate_canvas_write(canvas_id: str, auth) -> str`；`canvas_generation._registration_scope_id(canvas_id, user_id) -> int`（`RuntimeError` 表示解析不出）。
- Produces: `generated_media_router._canvas_import_scope(auth, canvas_id: int) -> int`（seam）；`generated_media_router._import_scope(auth, canvas_id: int | None) -> int`。

- [ ] **Step 1: 改测试（先红）**

`test_generated_media_import.py` 的 `_patch_scope_and_register` 改为同时打桩画布 scope：

```python
def _patch_scope_and_register(monkeypatch, captured):
    import app.services.library.generated_media_service as gm

    router_mod = _router_mod()

    async def _fake_scope(_auth):
        return 42

    async def _fake_canvas_scope(_auth, canvas_id):
        captured["canvas_scope_for"] = canvas_id
        return 77

    async def _fake_register(**kwargs):
        captured.update(kwargs)
        return {"id": 999}

    monkeypatch.setattr(router_mod, "_scope", _fake_scope)
    monkeypatch.setattr(router_mod, "_canvas_import_scope", _fake_canvas_scope)
    monkeypatch.setattr(gm, "register_generated_media", _fake_register)
```

`test_import_image_registers_and_returns_cover_url`（`canvas_id="123"`）末尾追加：

```python
    assert captured["canvas_scope_for"] == 123
    assert captured["scope_id"] == 77
```

`test_import_video_returns_stream_url`（`canvas_id=None`）末尾追加：

```python
    assert "canvas_scope_for" not in captured
    assert captured["scope_id"] == 42
```

文件末尾追加：

```python
@pytest.mark.asyncio
async def test_canvas_import_scope_gates_the_canvas_then_resolves_its_scope(
    monkeypatch,
):
    import sys

    import app.workflows.canvas_generation as cg

    router_mod = _router_mod()
    calls = []

    async def _fake_gate(canvas_id, auth):
        calls.append(("gate", canvas_id))
        return "777"

    async def _fake_registration_scope(canvas_id, user_id):
        calls.append(("scope", canvas_id, user_id))
        return 77

    monkeypatch.setattr(
        sys.modules["app.api.canvases_router"], "_gate_canvas_write", _fake_gate
    )
    monkeypatch.setattr(cg, "_registration_scope_id", _fake_registration_scope)

    assert await router_mod._canvas_import_scope(_Auth(), 123) == 77
    assert calls == [("gate", "123"), ("scope", 123, "u-uuid")]


@pytest.mark.asyncio
async def test_canvas_import_scope_unresolved_is_500(monkeypatch):
    import sys

    from fastapi import HTTPException

    import app.workflows.canvas_generation as cg

    router_mod = _router_mod()

    async def _fake_gate(canvas_id, auth):
        return "777"

    async def _unresolved(canvas_id, user_id):
        raise RuntimeError("scope_unresolved")

    monkeypatch.setattr(
        sys.modules["app.api.canvases_router"], "_gate_canvas_write", _fake_gate
    )
    monkeypatch.setattr(cg, "_registration_scope_id", _unresolved)

    with pytest.raises(HTTPException) as caught:
        await router_mod._canvas_import_scope(_Auth(), 123)
    assert caught.value.status_code == 500
```

（`app.api` 包会把 `canvases_router` 这个名字重绑成 APIRouter，所以两个测试都经 `sys.modules["app.api.canvases_router"]` 打桩。）

Run: `cd backend && uv run pytest tests/test_generated_media_import.py -v`
Expected: FAIL——`AttributeError: … has no attribute '_canvas_import_scope'`。

- [ ] **Step 2: 实现**

`generated_media_router.py`，在 `_scope` 之后加：

```python
async def _canvas_import_scope(auth, canvas_id: int) -> int:
    """The canvas's own scope, after the canvas write gate.

    A mask / brush / resize baked in a team board's editor belongs to that
    board — the same scope its crop / grid / outpaint register into
    (``canvas_derive_service``) and its generations register into.
    """
    import sys

    from app.workflows.canvas_generation import _registration_scope_id

    # ``app.api`` rebinds ``canvases_router`` to the APIRouter; the module
    # (and its patchable gate) lives in sys.modules.
    gate = sys.modules["app.api.canvases_router"]._gate_canvas_write
    await gate(str(canvas_id), auth)
    try:
        return await _registration_scope_id(canvas_id, str(auth.user_id))
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500, detail="canvas scope could not be resolved"
        ) from exc


async def _import_scope(auth, canvas_id: Optional[int]) -> int:
    """Where an import registers: the canvas's scope when it names one,
    otherwise the caller's personal team (unchanged)."""
    if canvas_id is None:
        return await _scope(auth)
    return await _canvas_import_scope(auth, canvas_id)
```

`import_generation` 里 `register_generated_media(` 调用的 `scope_id=await _scope(auth),` 改为

```python
            scope_id=await _import_scope(auth, canvas_id_int),
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_generated_media_import.py tests/test_generated_media_import_from_resource.py -v`
Expected: 全部 PASS。

- [ ] **Step 4: 格式化 + lint + 提交**

```bash
cd backend
F="app/api/generated_media_router.py tests/test_generated_media_import.py"
uv run black $F && uv run isort $F && uv run ruff check $F
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refs add backend/app/api/generated_media_router.py backend/tests/test_generated_media_import.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refs commit -m "fix(generated): 画布内导入先过画布写权限，产物注册进画布 scope"
```

- [ ] **Step 5: PR3 回归 + 开 PR**

```bash
cd backend && uv run pytest -q --ignore=tests/api --deselect tests/test_storage_migration_web_resource_files.py
git -C /Volumes/program/project-code/repos/nous-app-wt-derive-refs push -u origin fix/canvas-refs-follow-archive
gh pr create --repo iocrazy/nous-app --base master --head fix/canvas-refs-follow-archive \
  --title "fix(canvas): 画布引用跟着归档走；放大补读权限并落源 scope；画布导入进画布 scope" \
  --body "计划：docs/superpowers/plans/2026-09-10-canvas-derive-any-reference.md（Task 7–9）。三个既有缺陷：promote 写的画布引用下次保存即被抹掉；upscale 对源 id 零读权限校验且结果进个人空间；画布导入不校验画布写权限且进个人空间。"
```

CI 绿即合并，等部署后探 `readyz`。

---

## PR4 — `feat/canvas-editors-take-reference`

前置：PR2 **已部署到生产**（前端直接打新端点，端点不在线就是 404）。确认：`curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://api.nous.ink/api/v1/canvases/1/derive-crop -H 'Content-Type: application/json' -d '{}'` 返回 `401`（未登录）而不是 `404`。worktree：`git -C /Volumes/program/project-code/repos/nous-app worktree add /Volumes/program/project-code/repos/nous-app-wt-derive-frontend -b feat/canvas-editors-take-reference origin/master`，`cd …/frontend && npm ci`。

以下命令都在 `frontend/` 下执行。

### Task 10: 前端派生客户端 + `MediaItemEditor` 改为按 URL 派生

**Files:**
- Modify: `frontend/features/canvas-core/services/canvasService.ts`（在 `deriveOutpaint` 之后追加一节）
- Modify: `frontend/features/canvas-core/services/canvasService.test.ts`（文件末尾追加）
- Modify: `frontend/features/canvas-core/smart/mediaImport.ts:26`
- Modify: `frontend/features/canvas-core/smart/nodes/MediaItemEditor.tsx`（整文件替换）
- Modify: `frontend/features/canvas-core/smart/nodes/MediaItemEditor.test.tsx`（整文件替换）

**Interfaces:**
- Consumes: Task 6 的 HTTP 契约；文件内私有 `readEnvelope<T>(response)`；`apiFetch(path, { method, json })`（非 2xx 抛 `ApiError`，`message` 取 `ErrorResponse.error`）。
- Produces:
  - `interface CanvasDerivedImage { id: string; url: string; kind: 'image'; row: number | null; col: number | null }`
  - `interface CanvasDeriveOptions { nodeId?: string }`、`interface CanvasOutpaintOptions extends CanvasDeriveOptions { prompt?: string }`
  - `deriveCanvasCrop(canvasId: string, sourceUrl: string, region: CropRegion, opts?: CanvasDeriveOptions): Promise<CanvasDerivedImage>`
  - `deriveCanvasGrid(canvasId: string, sourceUrl: string, lines: GridLines, opts?: CanvasDeriveOptions): Promise<CanvasDerivedImage[]>`
  - `deriveCanvasOutpaint(canvasId: string, sourceUrl: string, padding: OutpaintPadding, opts?: CanvasOutpaintOptions): Promise<CanvasDerivedImage>`
  - `CanvasUploadRole = 'user_upload' | 'mask' | 'brush' | 'derived'`
  - `MediaItemEditor` props 不变；新增可见错误 `data-testid="media-edit-error"`。

- [ ] **Step 1: 写客户端失败测试**

`canvasService.test.ts`：顶部 import 列表加 `deriveCanvasCrop, deriveCanvasGrid, deriveCanvasOutpaint`，文件末尾追加：

```ts
describe('canvas derive (any image reference)', () => {
  const SOURCE = '/api/v1/generated-media/5/cover';
  const image = (id: string, row: number | null = null, col: number | null = null) => ({
    id,
    url: `/api/v1/generated-media/${id}/cover`,
    kind: 'image',
    row,
    col,
  });

  it('deriveCanvasCrop POSTs source_url + region + node_id to the canvas', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ images: [image('901')] }));
    const region: CropRegion = { x: 0.1, y: 0.2, width: 0.3, height: 0.4 };
    const result = await deriveCanvasCrop('4242', SOURCE, region, { nodeId: 'o1' });
    expect(result).toEqual(image('901'));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/canvases/4242/derive-crop');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({
      source_url: SOURCE,
      node_id: 'o1',
      region,
    });
  });

  it('deriveCanvasGrid returns every tile and omits node_id when absent', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ images: [image('901', 0, 0), image('902', 0, 1)] }),
    );
    const lines: GridLines = { xs: [0.5], ys: [] };
    const tiles = await deriveCanvasGrid('4242', SOURCE, lines);
    expect(tiles.map((t) => [t.id, t.row, t.col])).toEqual([
      ['901', 0, 0],
      ['902', 0, 1],
    ]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/canvases/4242/derive-grid');
    expect(JSON.parse(String(init?.body))).toEqual({ source_url: SOURCE, xs: [0.5], ys: [] });
  });

  it('deriveCanvasOutpaint sends padding and the prompt only when given', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ images: [image('901')] }));
    fetchMock.mockResolvedValueOnce(envelope({ images: [image('902')] }));
    const padding = { left: 0, top: 0, right: 0.1, bottom: 0 };
    await deriveCanvasOutpaint('4242', SOURCE, padding, { prompt: 'a windswept meadow' });
    await deriveCanvasOutpaint('4242', SOURCE, padding);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      source_url: SOURCE,
      left: 0,
      top: 0,
      right: 0.1,
      bottom: 0,
      prompt: 'a windswept meadow',
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body)).prompt).toBeUndefined();
  });

  it('surfaces the ErrorResponse message as ApiError', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          success: false,
          error: 'source image not found',
          code: 'http_404',
          request_id: 'req-1',
          details: null,
        }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const err = await deriveCanvasCrop('4242', SOURCE, {
      x: 0,
      y: 0,
      width: 1,
      height: 1,
    }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe('source image not found');
    expect((err as ApiError).status).toBe(404);
  });

  it('rejects a success envelope with no images', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ images: [] }));
    await expect(
      deriveCanvasCrop('4242', SOURCE, { x: 0, y: 0, width: 1, height: 1 }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run features/canvas-core/services/canvasService.test.ts`
Expected: FAIL——`deriveCanvasCrop is not a function`（或 TS 导入报错）。

- [ ] **Step 3: 实现客户端**

`canvasService.ts`，在 `deriveOutpaint` 函数之后追加：

```ts
// ============================================================
// Canvas derive — any image the canvas shows (2026-09-10)
// ============================================================

/** One image a canvas derive produced: a durable generated-media item. */
export interface CanvasDerivedImage {
  /** generated_media id (snowflake, string on the wire). */
  id: string;
  /** Always `/api/v1/generated-media/{id}/cover`. */
  url: string;
  kind: 'image';
  /** 0-based tile position for a grid derive; null otherwise. */
  row: number | null;
  col: number | null;
}

export interface CanvasDeriveOptions {
  /** The node the edit was made from — provenance on the registered row. */
  nodeId?: string;
}

export interface CanvasOutpaintOptions extends CanvasDeriveOptions {
  prompt?: string;
}

function canvasDerivePayload(
  sourceUrl: string,
  opts: CanvasDeriveOptions,
): Record<string, unknown> {
  const payload: Record<string, unknown> = { source_url: sourceUrl };
  if (opts.nodeId) payload.node_id = opts.nodeId;
  return payload;
}

async function postCanvasDerive(
  canvasId: string,
  op: 'crop' | 'grid' | 'outpaint',
  payload: Record<string, unknown>,
): Promise<CanvasDerivedImage[]> {
  const response = await apiFetch(`/api/v1/canvases/${canvasId}/derive-${op}`, {
    method: 'POST',
    json: payload,
  });
  const data = await readEnvelope<{ images?: CanvasDerivedImage[] }>(response);
  if (!Array.isArray(data.images) || data.images.length === 0) {
    throw new ApiError(`canvas derive-${op} returned no images`, response.status);
  }
  return data.images;
}

/**
 * Crop whatever image `sourceUrl` names — a generation, an upload, a library
 * asset — into a new generated-media item in the canvas's space. The backend
 * checks the canvas write and the source read; refusals arrive as ApiError.
 */
export async function deriveCanvasCrop(
  canvasId: string,
  sourceUrl: string,
  region: CropRegion,
  opts: CanvasDeriveOptions = {},
): Promise<CanvasDerivedImage> {
  const [image] = await postCanvasDerive(canvasId, 'crop', {
    ...canvasDerivePayload(sourceUrl, opts),
    region,
  });
  return image;
}

/** Split `sourceUrl` along normalized lines; one item per tile, row-major. */
export async function deriveCanvasGrid(
  canvasId: string,
  sourceUrl: string,
  lines: GridLines,
  opts: CanvasDeriveOptions = {},
): Promise<CanvasDerivedImage[]> {
  return postCanvasDerive(canvasId, 'grid', {
    ...canvasDerivePayload(sourceUrl, opts),
    xs: lines.xs,
    ys: lines.ys,
  });
}

/** Extend `sourceUrl`'s canvas (blur fill) into a new item. */
export async function deriveCanvasOutpaint(
  canvasId: string,
  sourceUrl: string,
  padding: OutpaintPadding,
  opts: CanvasOutpaintOptions = {},
): Promise<CanvasDerivedImage> {
  const payload: Record<string, unknown> = {
    ...canvasDerivePayload(sourceUrl, opts),
    left: padding.left,
    top: padding.top,
    right: padding.right,
    bottom: padding.bottom,
  };
  if (opts.prompt) payload.prompt = opts.prompt;
  const [image] = await postCanvasDerive(canvasId, 'outpaint', payload);
  return image;
}
```

`mediaImport.ts:26` 改为：

```ts
export type CanvasUploadRole = 'user_upload' | 'mask' | 'brush' | 'derived';
```

并在该类型上方注释末尾补一句：`'derived' is a product the user asked for (a client-baked resize) — visible, like the server-side crop / grid / outpaint beside it.`

Run: `npx vitest run features/canvas-core/services/canvasService.test.ts`
Expected: PASS（旧 `deriveCrop` 等测试仍在、仍绿；它们在 PR5 删除）。

- [ ] **Step 4: 重写 `MediaItemEditor` 测试（先红）**

`MediaItemEditor.test.tsx` 整文件替换为：

```tsx
/**
 * MediaItemEditor (B3) — every commit APPENDS a product onto the card.
 * Crop / outpaint / split derive from the item's OWN url (no promote), so
 * they work on any image; brush / mask / resize bake client-side.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/canvasService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/canvasService')>();
  return {
    ...actual,
    deriveCanvasCrop: vi.fn(async () => ({
      id: '901',
      url: '/api/v1/generated-media/901/cover',
      kind: 'image',
      row: null,
      col: null,
    })),
  };
});
vi.mock('../mediaImport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../mediaImport')>();
  return { ...actual, importCanvasMedia: vi.fn() };
});

import { ApiError } from '../../../../services/apiClient';
import { deriveCanvasCrop } from '../../services/canvasService';
import { MediaItemEditor } from './MediaItemEditor';

const ITEM = { url: '/api/v1/generated-media/5/cover', kind: 'image' as const, name: 'a.png' };

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderEditor(
  canvasId: string | null,
  mode: 'crop' | 'preview' = 'crop',
  onAppend = vi.fn(),
  onClose = vi.fn(),
) {
  render(
    <MediaItemEditor
      canvasId={canvasId}
      nodeId="m1"
      item={ITEM}
      mode={mode}
      onClose={onClose}
      onAppend={onAppend}
    />,
  );
  return { onAppend, onClose };
}

describe('MediaItemEditor', () => {
  it('crop derives from the item url and appends the durable product', async () => {
    const { onAppend, onClose } = renderEditor('4242');
    fireEvent.click(screen.getByTestId('editor-apply'));
    await waitFor(() => expect(onAppend).toHaveBeenCalledTimes(1));
    expect(deriveCanvasCrop).toHaveBeenCalledWith(
      '4242',
      '/api/v1/generated-media/5/cover',
      expect.objectContaining({ width: 1, height: 1 }),
      { nodeId: 'm1' },
    );
    expect(onAppend).toHaveBeenCalledWith({
      url: '/api/v1/generated-media/901/cover',
      kind: 'image',
      name: 'a.png',
      id: '901',
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('offers derive tabs immediately — no promote round trip', () => {
    renderEditor('4242');
    expect(screen.getByTestId('editor-tab-crop')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-split')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-outpaint')).toBeInTheDocument();
  });

  it('without a canvas only the client-side tabs remain', () => {
    renderEditor(null, 'preview');
    expect(screen.queryByTestId('editor-tab-crop')).toBeNull();
    expect(screen.getByTestId('editor-tab-brush')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-resize')).toBeInTheDocument();
  });

  it('a refused derive shows the server message and keeps the editor open', async () => {
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new ApiError('source image not found', 404),
    );
    const { onAppend, onClose } = renderEditor('4242');
    fireEvent.click(screen.getByTestId('editor-apply'));
    const banner = await screen.findByTestId('media-edit-error');
    expect(banner.textContent).toBe('source image not found');
    expect(onAppend).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
```

（tab 的 `data-testid` 是 `` `editor-tab-${m.mode}` ``，`UnifiedImageEditor.tsx:85-91` 的模式名为 preview / crop / outpaint / mask / brush / resize / split。）

Run: `npx vitest run features/canvas-core/smart/nodes/MediaItemEditor.test.tsx`
Expected: FAIL——旧组件仍在调 `ensureResourceId`，crop tab 不会立即出现、`deriveCanvasCrop` 未被调用。

- [ ] **Step 5: 重写 `MediaItemEditor`**

`MediaItemEditor.tsx` 整文件替换为：

```tsx
// features/canvas-core/smart/nodes/MediaItemEditor.tsx
//
// B3 — the media card's edit pipeline. Opens the UnifiedImageEditor on one
// item; every commit APPENDS the product as a new item on the card
// (non-destructive). Crop / outpaint / split derive server-side from the
// item's OWN url — whatever the image is and wherever it came from — and
// come back as durable generated-media items in the canvas's space; brush /
// mask / resize bake client-side. The server derives need a canvas to file
// the product under, so without one only the client-side tabs show.

import { useState } from 'react';

import {
  deriveCanvasCrop,
  deriveCanvasGrid,
  deriveCanvasOutpaint,
  type CanvasDerivedImage,
} from '../../services/canvasService';
import { bakeResize } from '../../editor/imageBake';
import { strokesToMaskPngBase64 } from '../../editor/maskExport';
import {
  UnifiedImageEditor,
  type EditorMode,
} from '../../editor/UnifiedImageEditor';
import { importCanvasMedia, type CanvasUploadRole } from '../mediaImport';
import type { GeneratedImageRef } from '../types';

export interface MediaItemEditorProps {
  canvasId: string | null;
  nodeId: string;
  item: GeneratedImageRef;
  mode: EditorMode;
  onClose(): void;
  /** Receives every produced item to append onto the card. */
  onAppend(item: GeneratedImageRef): void;
}

export function MediaItemEditor({
  canvasId,
  nodeId,
  item,
  mode,
  onClose,
  onAppend,
}: MediaItemEditorProps) {
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = (work: () => Promise<void>) => {
    void (async () => {
      try {
        setCommitting(true);
        setError(null);
        await work();
        onClose();
      } catch (err) {
        console.error('[MediaItemEditor] edit failed:', err);
        // Stay open with the reason on screen: a refused derive that closed
        // the editor silently is indistinguishable from one that worked.
        setError(err instanceof Error ? err.message : 'Edit failed');
      } finally {
        setCommitting(false);
      }
    })();
  };

  const appendDerived = (image: CanvasDerivedImage) => {
    onAppend({ url: image.url, kind: 'image', name: item.name, id: image.id });
  };
  const appendBlob = async (blob: Blob, name: string, role: CanvasUploadRole) => {
    const file = new File([blob], name, { type: 'image/png' });
    onAppend(await importCanvasMedia(file, canvasId, nodeId, role));
  };

  return (
    <>
      <UnifiedImageEditor
        open
        src={item.url}
        alt={item.name ?? ''}
        initialMode={mode}
        onClose={onClose}
        committing={committing}
        onCropCommit={
          canvasId
            ? (region) =>
                run(async () => {
                  appendDerived(
                    await deriveCanvasCrop(canvasId, item.url, region, { nodeId }),
                  );
                })
            : undefined
        }
        onOutpaintCommit={
          canvasId
            ? (padding, prompt) =>
                run(async () => {
                  appendDerived(
                    await deriveCanvasOutpaint(canvasId, item.url, padding, {
                      nodeId,
                      prompt,
                    }),
                  );
                })
            : undefined
        }
        onSplitCommit={
          canvasId
            ? (lines) =>
                run(async () => {
                  const tiles = await deriveCanvasGrid(canvasId, item.url, lines, {
                    nodeId,
                  });
                  tiles.forEach(appendDerived);
                })
            : undefined
        }
        onMaskCommit={(strokes, size) =>
          run(async () => {
            // IC 生成遮罩节点: the black/white mask itself becomes a new item.
            const b64 = strokesToMaskPngBase64(strokes, size.width, size.height);
            const bin = atob(b64.split(',').pop() ?? b64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            await appendBlob(new Blob([bytes], { type: 'image/png' }), 'mask.png', 'mask');
          })
        }
        onBrushCommit={(composite: Blob) =>
          run(async () => {
            await appendBlob(composite, 'brush.png', 'brush');
          })
        }
        onResizeCommit={(scale: number) =>
          run(async () => {
            const blob = await bakeResize(item.url, scale);
            await appendBlob(blob, 'resized.png', 'derived');
          })
        }
      />
      {error && (
        <div
          data-testid="media-edit-error"
          role="alert"
          className="fixed left-1/2 top-6 z-[60] -translate-x-1/2 rounded bg-danger px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {error}
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 6: 跑测试确认通过 + 类型检查**

Run: `npx vitest run features/canvas-core/smart/nodes/MediaItemEditor.test.tsx features/canvas-core/services/canvasService.test.ts && npx tsc --noEmit -p .`
Expected: PASS，tsc 无新增错误。（唯一挂载点 `MediaNodeView.tsx:448` 传的 `canvasId` 取自 `useCanvasCoreStore((s) => s.canvasId)`，画布内恒非空。）

- [ ] **Step 7: lint + 提交**

```bash
npx eslint features/canvas-core/services/canvasService.ts features/canvas-core/services/canvasService.test.ts features/canvas-core/smart/mediaImport.ts features/canvas-core/smart/nodes/MediaItemEditor.tsx features/canvas-core/smart/nodes/MediaItemEditor.test.tsx
W=/Volumes/program/project-code/repos/nous-app-wt-derive-frontend
git -C $W add frontend/features/canvas-core/services/canvasService.ts frontend/features/canvas-core/services/canvasService.test.ts frontend/features/canvas-core/smart/mediaImport.ts frontend/features/canvas-core/smart/nodes/MediaItemEditor.tsx frontend/features/canvas-core/smart/nodes/MediaItemEditor.test.tsx
git -C $W commit -m "feat(canvas): 媒体卡编辑器按图片 URL 派生，不再先入库；失败原因可见"
```

### Task 11: `OutputNodeView` 按"正在编辑的图"派生，删掉自动 promote

改动面（`frontend/features/canvas-core/smart/nodes/OutputNodeView.tsx`，行号以 origin/master `4a8bf3ab` 为准，实施前 `grep -n` 复核）：

| 位置 | 现在 | 改成 |
|---|---|---|
| `:13-14` import `getResourceFileUrl` / `getSupabaseClient` | 只给 `buildPreviewUrl` 用 | 删 |
| `:24-29` import `deriveCrop/deriveGrid/deriveMaskCutout/deriveOutpaint` | 旧资源派生 | 换成 `deriveCanvasCrop/deriveCanvasGrid/deriveCanvasOutpaint` |
| `:43` import `ensureResourceId` | 自动 promote | 删（`:32` 的 `genIdFromDurableUrl` 保留，As Asset 在用） |
| `:75-85` `buildPreviewUrl` | 把会话 token 拼进 URL 写进节点数据 | 删 |
| `:90` 解构 `resource_id` | 编辑门控 + Saved 徽章 | 删 |
| `:290-293` `primaryImageUrl` / `canCrop` | | 上移到 `:130` `editingUrl` 声明之后，并加 `editSourceUrl` |
| `:345-362` `canSplit` + promote effect | 必须先入库 | 换成 `canDerive = canCrop && !!canvasId` |
| `:376-412` `handleCommit` | 无 resource_id 只写 `crop_region`（假裁剪）；有则按 resource_id 派生 | 按 `editSourceUrl` 派生，原地替换被编辑的那张图 |
| `:413-418` / `:486-491` / `:542-546` 三个 open* | 全被 `canSplit` 卡住（遮罩按钮在无 resource_id 时是死按钮） | 扩图 / 切图用 `canDerive`；遮罩用 `canCrop` |
| `:426-485` `handleOutpaintCommit` / `:554-606` `handleGridCommit` | 按 resource_id | 按 `editSourceUrl`；新节点只带 `preview_url` |
| `:225-283` 画笔 / 缩放 | 守卫与缩放源都是 `preview_url`（历史卡、网格第 2 张图编辑错图或静默不动） | 用 `editSourceUrl`；缩放产物 role `'derived'` |
| `:655-670` 工具栏、`:891-907` 编辑器 props、`:945-956` 灯箱 editActions | `canSplit` | 见 Step 5 |
| `:692-696` `Saved` 徽章 | `resource_id` 存在即显示 | 删 |

**Files:**
- Create: `frontend/features/canvas-core/smart/swapEditedImage.ts`
- Create: `frontend/features/canvas-core/smart/swapEditedImage.test.ts`
- Modify: `frontend/features/canvas-core/smart/nodes/OutputNodeView.tsx`
- Modify: `frontend/features/canvas-core/smart/nodes/OutputNodeView.derive.test.tsx`（整文件替换）
- Modify: `frontend/features/canvas-core/smart/nodes/OutputNodeView.grid.test.tsx`
- Modify: `frontend/features/canvas-core/smart/nodes/OutputNodeView.outpaint.test.tsx`
- Modify: `frontend/features/canvas-core/smart/nodes/OutputNodeView.mask.test.tsx`
- Modify: `frontend/features/canvas-core/smart/nodes/OutputNodeView.promote.test.tsx`

**Interfaces:**
- Consumes: Task 10 的 `deriveCanvasCrop` / `deriveCanvasGrid` / `deriveCanvasOutpaint` / `CanvasDerivedImage`、`CanvasUploadRole` 含 `'derived'`；`GeneratedImageRef`（`smart/types.ts:282`）；`createOutputNode(data: Partial<OutputNodeData>, opts)`。
- Produces:
  - `swapEditedImage(slots: { preview_url?: string | null; images?: GeneratedImageRef[] | null }, from: string, to: { url: string; id: string }): EditedImagePatch`
  - `interface EditedImagePatch { preview_url?: string; resource_id?: null; crop_region?: null; images?: GeneratedImageRef[] }`
  - 语义：`preview_url === from` → 换 `preview_url`，并清掉只描述旧图的 `resource_id` 与 `crop_region`；`images[]` 里 `url === from` 的项换成新 url + 新 id（保留 name/kind）；两处都没命中 → 追加到 `images`（绝不静默无变化）。

- [ ] **Step 1: 纯函数的失败测试**

`smart/swapEditedImage.test.ts`：

```ts
import { describe, expect, it } from 'vitest';

import { swapEditedImage } from './swapEditedImage';

const TO = { url: '/api/v1/generated-media/901/cover', id: '901' };

describe('swapEditedImage', () => {
  it('replaces the primary preview and drops what described the old picture', () => {
    expect(
      swapEditedImage(
        {
          preview_url: '/api/v1/generated-media/5/cover',
          images: [{ url: '/api/v1/generated-media/5/cover', kind: 'image', name: 'a.png' }],
        },
        '/api/v1/generated-media/5/cover',
        TO,
      ),
    ).toEqual({
      preview_url: TO.url,
      resource_id: null,
      crop_region: null,
      images: [{ url: TO.url, kind: 'image', name: 'a.png', id: '901' }],
    });
  });

  it('replaces only the grid image that was edited', () => {
    const images = [
      { url: '/api/v1/generated-media/5/cover', kind: 'image' as const },
      { url: '/api/v1/generated-media/6/cover', kind: 'image' as const, name: 'mask.png' },
    ];
    expect(
      swapEditedImage(
        { preview_url: '/api/v1/generated-media/5/cover', images },
        '/api/v1/generated-media/6/cover',
        TO,
      ),
    ).toEqual({
      images: [images[0], { url: TO.url, kind: 'image', name: 'mask.png', id: '901' }],
    });
    expect(images[1].url).toBe('/api/v1/generated-media/6/cover');
  });

  it('a history node with no preview_url swaps inside images', () => {
    expect(
      swapEditedImage(
        { preview_url: null, images: [{ url: '/x/5', kind: 'image' }] },
        '/x/5',
        TO,
      ),
    ).toEqual({ images: [{ url: TO.url, kind: 'image', id: '901' }] });
  });

  it('never returns a no-op: an unmatched source appends the product', () => {
    expect(swapEditedImage({ preview_url: '/x/1', images: null }, '/x/2', TO)).toEqual({
      images: [{ url: TO.url, kind: 'image', id: '901' }],
    });
  });
});
```

Run: `npx vitest run features/canvas-core/smart/swapEditedImage.test.ts`
Expected: FAIL——无法解析 `./swapEditedImage`。

- [ ] **Step 2: 实现纯函数**

`smart/swapEditedImage.ts`：

```ts
// features/canvas-core/smart/swapEditedImage.ts
//
// An in-place edit (crop) replaces THE image that was edited — the primary
// preview, one grid item, or an item of a history node that has no preview —
// with its derived copy. Pure: returns the node-data patch.

import type { GeneratedImageRef } from './types';

export interface EditedImagePatch {
  preview_url?: string;
  /** Cleared with the primary: both only ever described the old picture. */
  resource_id?: null;
  crop_region?: null;
  images?: GeneratedImageRef[];
}

export function swapEditedImage(
  slots: { preview_url?: string | null; images?: GeneratedImageRef[] | null },
  from: string,
  to: { url: string; id: string },
): EditedImagePatch {
  const patch: EditedImagePatch = {};
  if (slots.preview_url === from) {
    patch.preview_url = to.url;
    patch.resource_id = null;
    patch.crop_region = null;
  }
  const images = Array.isArray(slots.images) ? slots.images : [];
  if (images.some((img) => img?.url === from)) {
    patch.images = images.map((img) =>
      img?.url === from ? { ...img, url: to.url, id: to.id } : img,
    );
  } else if (patch.preview_url === undefined) {
    // Nothing matched: keep the product rather than let the edit vanish.
    patch.images = [...images, { url: to.url, kind: 'image', id: to.id }];
  }
  return patch;
}
```

Run: `npx vitest run features/canvas-core/smart/swapEditedImage.test.ts`
Expected: PASS。

- [ ] **Step 3: 改组件测试（先红）**

**(a)** `OutputNodeView.derive.test.tsx` 整文件替换为：

```tsx
import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

vi.mock('../../services/canvasService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/canvasService')>();
  return { ...actual, deriveCanvasCrop: vi.fn() };
});

const { deriveCanvasCrop } = await import('../../services/canvasService');

const SOURCE = '/api/v1/generated-media/5/cover';
const DERIVED = {
  id: '901',
  url: '/api/v1/generated-media/901/cover',
  kind: 'image',
  row: null,
  col: null,
};
const ORIGINAL_GET_BOUNDING = HTMLElement.prototype.getBoundingClientRect;

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
  HTMLElement.prototype.getBoundingClientRect = function fakeRect() {
    return {
      x: 0, y: 0, width: 1000, height: 500, top: 0, left: 0, bottom: 500, right: 1000,
      toJSON: () => ({}),
    } as DOMRect;
  };
  (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockReset();
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = ORIGINAL_GET_BOUNDING;
  useCanvasCoreStore.getState().reset();
});

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

const baseProps = {
  type: 'output',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  // Selected: the floating toolbar mounts only while pinned or hovered.
  selected: true,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 260,
  height: 100,
  zIndex: 0,
} as const;

function seedImageOutput(legacy: Record<string, unknown> = {}) {
  const fullData = {
    kind: 'image',
    preview_text: '',
    preview_url: SOURCE,
    images: [{ url: SOURCE, kind: 'image' }],
    crop_region: null,
    ...legacy,
  };
  useCanvasCoreStore.setState({
    nodes: [{ id: 'o1', type: 'output', data: fullData, position: { x: 0, y: 0 } }],
  });
  return fullData;
}

function nodeData(): Record<string, unknown> {
  return (useCanvasCoreStore.getState().nodes[0] as { data: Record<string, unknown> }).data;
}

function cropAndApply(fullData: Record<string, unknown>) {
  render(
    <Wrap>
      <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
    </Wrap>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Crop' }));
  fireEvent.click(screen.getByTestId('editor-apply'));
}

describe('OutputNodeView — crop derives from the shown image', () => {
  it('derives by url (no resource_id needed) and swaps the image in place', async () => {
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockResolvedValueOnce(DERIVED);
    cropAndApply(seedImageOutput());

    await waitFor(() => expect(deriveCanvasCrop).toHaveBeenCalledTimes(1));
    expect(deriveCanvasCrop).toHaveBeenCalledWith(
      '4242',
      SOURCE,
      expect.objectContaining({ x: 0, y: 0, width: 1, height: 1 }),
      { nodeId: 'o1' },
    );
    await waitFor(() => expect(nodeData().preview_url).toBe(DERIVED.url));
    expect(nodeData().images).toEqual([{ url: DERIVED.url, kind: 'image', id: '901' }]);
    expect(nodeData().crop_region).toBeNull();
    // Nothing session-bearing is persisted into canvas data.
    expect(JSON.stringify(useCanvasCoreStore.getState().nodes)).not.toMatch(/token=/);
    await waitFor(() =>
      expect(screen.queryByTestId('unified-image-editor')).not.toBeInTheDocument(),
    );
  });

  it('a legacy promoted node loses the fields that described the old picture', async () => {
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockResolvedValueOnce(DERIVED);
    cropAndApply(
      seedImageOutput({
        resource_id: 'source-123',
        crop_region: { x: 0.1, y: 0.1, width: 0.5, height: 0.5 },
      }),
    );

    await waitFor(() => expect(nodeData().preview_url).toBe(DERIVED.url));
    expect(deriveCanvasCrop).toHaveBeenCalledWith(
      '4242',
      SOURCE,
      expect.objectContaining({ x: 0.1, y: 0.1, width: 0.5, height: 0.5 }),
      { nodeId: 'o1' },
    );
    expect(nodeData().resource_id).toBeNull();
    expect(nodeData().crop_region).toBeNull();
  });

  it('a refused derive shows the server message and changes nothing', async () => {
    (deriveCanvasCrop as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('source image not found'),
    );
    cropAndApply(seedImageOutput());

    await waitFor(() => expect(deriveCanvasCrop).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    const banner = await screen.findByTestId('crop-commit-error');
    expect(banner.textContent).toMatch(/source image not found/);
    expect(nodeData().preview_url).toBe(SOURCE);
  });
});
```

**(b)** `OutputNodeView.grid.test.tsx`：
- `vi.mock('../../services/canvasService', …)` 里 `deriveGrid: vi.fn()` 改为 `deriveCanvasGrid: vi.fn()`；删除 `resourceService` 与 `supabaseClient` 两个 `vi.mock`；`const { deriveGrid } = await import(…)` 改为 `const { deriveCanvasGrid } = …`；`beforeEach` 里的 `mockReset` 同步改名。
- `fakeTile` 整个替换为：

```tsx
function fakeTile(row: number, col: number) {
  const id = `90${row}${col}`;
  return { id, url: `/api/v1/generated-media/${id}/cover`, kind: 'image', row, col };
}
```

- `seedImageOutput(resourceId)` 改为无参，data 为 `{ kind: 'image', preview_text: '', preview_url: '/api/v1/generated-media/5/cover', crop_region: null }`（去掉 `resource_id`）。
- `describe('OutputNodeView — Split button', …)` 整块替换为：

```tsx
describe('OutputNodeView — Split button', () => {
  it('shows for any image output on a canvas — no resource_id needed', () => {
    const fullData = seedImageOutput();
    render(
      <Wrap>
        <OutputNodeView {...baseProps} selected id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    expect(screen.getByRole('button', { name: 'Split' })).toBeInTheDocument();
  });
});
```

- `describe('OutputNodeView — grid commit spawns tile nodes', …)` 整块替换为：

```tsx
describe('OutputNodeView — grid commit spawns tile nodes', () => {
  it('derives from the shown image and adds one output node per tile', async () => {
    const fullData = seedImageOutput();
    (deriveCanvasGrid as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      fakeTile(0, 0),
      fakeTile(0, 1),
      fakeTile(1, 0),
      fakeTile(1, 1),
    ]);
    render(
      <Wrap>
        <OutputNodeView {...baseProps} selected id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Split' }));
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    fireEvent.click(screen.getByTestId('editor-apply'));

    await waitFor(() => expect(deriveCanvasGrid).toHaveBeenCalledTimes(1));
    expect(deriveCanvasGrid).toHaveBeenCalledWith(
      '4242',
      '/api/v1/generated-media/5/cover',
      { xs: [0.5], ys: [0.5] },
      { nodeId: 'o1' },
    );
    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(5));
    const nodes = useCanvasCoreStore.getState().nodes as Array<{
      id: string;
      type: string;
      position: { x: number; y: number };
      data: Record<string, unknown>;
    }>;
    expect(nodes[0].id).toBe('o1');
    expect(nodes[0].data.preview_url).toBe('/api/v1/generated-media/5/cover');
    const tiles = nodes.slice(1);
    expect(tiles.every((node) => node.type === 'output')).toBe(true);
    expect(tiles.map((node) => node.data.preview_url)).toEqual([
      '/api/v1/generated-media/900/cover',
      '/api/v1/generated-media/901/cover',
      '/api/v1/generated-media/910/cover',
      '/api/v1/generated-media/911/cover',
    ]);
    expect(tiles[0].position.y).toBe(tiles[1].position.y);
    expect(tiles[1].position.x).toBeGreaterThan(tiles[0].position.x);
    expect(tiles[2].position.y).toBeGreaterThan(tiles[0].position.y);
    expect(tiles[2].position.x).toBe(tiles[0].position.x);
    expect(tiles[0].position.x).toBeGreaterThan(nodes[0].position.x);
    await waitFor(() =>
      expect(screen.queryByTestId('unified-image-editor')).not.toBeInTheDocument(),
    );
  });

  it('shows an error banner + keeps the modal open when the derive rejects', async () => {
    const fullData = seedImageOutput();
    (deriveCanvasGrid as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('at least one split line is required'),
    );
    render(
      <Wrap>
        <OutputNodeView {...baseProps} selected id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Split' }));
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    fireEvent.click(screen.getByTestId('editor-apply'));

    await waitFor(() => expect(deriveCanvasGrid).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    const banner = await screen.findByTestId('grid-commit-error');
    expect(banner.textContent).toMatch(/split line/);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });
});
```

- `describe('generation placeholders (P0-3)', …)` 不动。

**(c)** `OutputNodeView.outpaint.test.tsx`：
- mock 改 `deriveCanvasOutpaint: vi.fn()`，删 `resourceService` / `supabaseClient` 两个 mock，`await import` 与 `mockReset` 同步改名。
- `seedImageOutput(resourceId)` 改无参，data 为 `{ kind: 'image', preview_text: 'a windswept meadow', preview_url: '/api/v1/generated-media/5/cover', crop_region: null }`。
- `describe('OutputNodeView — Expand button', …)` 整块替换为：

```tsx
describe('OutputNodeView — Expand button', () => {
  it('shows for any image output on a canvas — no resource_id needed', () => {
    const fullData = seedImageOutput();
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    expect(screen.getByRole('button', { name: 'Expand' })).toBeInTheDocument();
  });
});
```

- `describe('OutputNodeView — outpaint commit spawns the extended node', …)` 里：两个用例的 `seedImageOutput('source-123')` → `seedImageOutput()`；第一个用例的 `mockResolvedValueOnce({...resource row...})` 换成 `mockResolvedValueOnce({ id: '901', url: '/api/v1/generated-media/901/cover', kind: 'image', row: null, col: null })`；原来解构 `[sourceId, padding, opts]` 的断言换成

```tsx
    const [canvasId, sourceUrl, padding, opts] = (
      deriveCanvasOutpaint as ReturnType<typeof vi.fn>
    ).mock.calls[0];
    expect(canvasId).toBe('4242');
    expect(sourceUrl).toBe('/api/v1/generated-media/5/cover');
    expect(padding.right).toBeCloseTo(0.1);
    expect(padding.left).toBe(0);
    expect(opts).toEqual({ nodeId: 'o1', prompt: 'a windswept meadow' });
```

  `extended.data.resource_id` / `preview_url` 两条断言换成

```tsx
    expect(extended.data.preview_url).toBe('/api/v1/generated-media/901/cover');
```

  第二个用例（reject）保留，mock 名改为 `deriveCanvasOutpaint`。

**(d)** `OutputNodeView.mask.test.tsx`：在 `describe('OutputNodeView — Mask button', …)` 里追加（遮罩按钮以前在无 resource_id 时显示却点不开）：

```tsx
  it('opens the mask editor without a resource_id', () => {
    const without = seedImageOutput(null);
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={without} />
      </Wrap>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Mask' }));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    expect(screen.getByTestId('mask-brush-tool')).toBeInTheDocument();
  });
```

**(e)** `OutputNodeView.promote.test.tsx`：
- 顶部注释第 (2) 条改为 "(2) opening the editor must NOT promote — editors derive from the image url (2026-09-10)"。
- 在 `vi.mock('../mediaEditBridge', …)` 之后加

```tsx
vi.mock('../../services/canvasService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/canvasService')>();
  return {
    ...actual,
    deriveCanvasCrop: vi.fn(async () => ({
      id: '901',
      url: '/api/v1/generated-media/901/cover',
      kind: 'image',
      row: null,
      col: null,
    })),
  };
});
import { deriveCanvasCrop } from '../../services/canvasService';
```

- 用例 `'opening the editor on a generated image promotes it to a resource'` 整个替换为：

```tsx
  it('opening the editor never promotes the image into the library', async () => {
    render(<ReactFlowProvider><OutputNodeView {...props()} /></ReactFlowProvider>);
    fireEvent.doubleClick(screen.getByTestId('smart-output-body'));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-split')).toBeInTheDocument();
    await Promise.resolve();
    expect(ensureResourceId).not.toHaveBeenCalled();
    const data = (useCanvasCoreStore.getState().nodes[0] as { data: { resource_id?: string } }).data;
    expect(data.resource_id).toBeUndefined();
  });
```

- 文件末尾追加：

```tsx
it('grid dblclick then crop derives from THAT image and swaps only it', async () => {
  const node = useCanvasCoreStore.getState().nodes[0] as { data: Record<string, unknown> };
  node.data = {
    ...node.data,
    images: [
      { url: '/api/v1/generated-media/9/cover', kind: 'image' },
      { url: '/api/v1/generated-media/10/cover', kind: 'image', name: 'mask.png' },
    ],
  };
  render(<ReactFlowProvider><OutputNodeView {...props()} /></ReactFlowProvider>);
  fireEvent.doubleClick(screen.getAllByAltText(/Generated|mask/i)[1]);
  fireEvent.click(screen.getByTestId('editor-tab-crop'));
  fireEvent.click(screen.getByTestId('editor-apply'));

  await vi.waitFor(() =>
    expect(deriveCanvasCrop).toHaveBeenCalledWith(
      'c1',
      '/api/v1/generated-media/10/cover',
      expect.anything(),
      { nodeId: 'o1' },
    ),
  );
  await vi.waitFor(() => {
    const data = (useCanvasCoreStore.getState().nodes[0] as {
      data: { preview_url: string; images: Array<{ url: string }> };
    }).data;
    expect(data.images.map((i) => i.url)).toEqual([
      '/api/v1/generated-media/9/cover',
      '/api/v1/generated-media/901/cover',
    ]);
    expect(data.preview_url).toBe('/api/v1/generated-media/9/cover');
  });
});
```

Run: `npx vitest run features/canvas-core/smart/nodes/OutputNodeView.derive.test.tsx features/canvas-core/smart/nodes/OutputNodeView.grid.test.tsx features/canvas-core/smart/nodes/OutputNodeView.outpaint.test.tsx features/canvas-core/smart/nodes/OutputNodeView.mask.test.tsx features/canvas-core/smart/nodes/OutputNodeView.promote.test.tsx`
Expected: 新 / 改过的用例 FAIL（组件仍调旧 `deriveCrop` 等、Split/Expand 在无 resource_id 时不显示、遮罩点不开、打开编辑器会调 `ensureResourceId`）；未改的用例 PASS。

- [ ] **Step 4: 改组件——import、状态、门控**

1. 删除 `:13-14` 两行 import（`getResourceFileUrl`、`getSupabaseClient`）与 `:43` 的 `import { ensureResourceId } from '../mediaEditBridge';`。
2. `:24-29` 的 import 改为：

```tsx
import {
  deriveCanvasCrop,
  deriveCanvasGrid,
  deriveCanvasOutpaint,
} from '../../services/canvasService';
```

   并在 `import { resolveSourceUrls } …` 附近加 `import { swapEditedImage } from '../swapEditedImage';`。
3. 删除 `buildPreviewUrl` 整个函数（`:72-85` 含注释）。
4. 解构里删掉 `resource_id,`。
5. 把 `:286-293` 的 `primaryImageUrl` / `canCrop`（含上方注释）**剪切**到 `const [editingUrl, setEditingUrl] = useState<string | null>(null);` 之后，并紧接着加：

```tsx
  // The image an edit acts on: the grid item that was double-clicked, else the
  // primary. Every derive keys on THIS url — never on the node's legacy
  // resource_id, which only ever named the primary, and only after a promote.
  const editSourceUrl = editingUrl ?? primaryImageUrl;
  // Leaving the editor forgets which item was being edited; otherwise the
  // toolbar's Crop would reopen on a grid item double-clicked long ago.
  useEffect(() => {
    if (editorMode === null) setEditingUrl(null);
  }, [editorMode]);
```

6. `:345-362`（`canSplit` 注释 + 定义 + 自动 promote 的 `useEffect`）整段替换为：

```tsx
  // Crop / Expand / Split derive server-side and file the product under this
  // canvas — any displayable image qualifies, wherever it came from.
  const canDerive = canCrop && !!canvasId;
```

7. `openOutpaintEditor` 与 `openGridEditor` 里的 `canSplit` → `canDerive`（函数体与依赖数组各一处）；`openMaskEditor` 里的 `canSplit` → `canCrop`（遮罩是纯前端烘焙）。

- [ ] **Step 5: 改组件——四个提交处理器**

`handleBrushCommit`：守卫 `if (!preview_url) return;` → `if (!editSourceUrl) return;`；依赖数组 `preview_url` → `editSourceUrl`。

`handleResizeCommit` 整个替换为：

```tsx
  const handleResizeCommit = useCallback(
    (scale: number) => {
      if (!editSourceUrl) return;
      void (async () => {
        try {
          setPixCommitting(true);
          const blob = await bakeResize(editSourceUrl, scale);
          const file = new File([blob], 'resized.png', { type: 'image/png' });
          // A resize is a product the user asked for — visible in the inbox,
          // same role as the server-side derives.
          const item = await importCanvasMedia(file, canvasId, id, 'derived');
          patchData({
            images: [
              ...((images as Array<{ url: string }>) ?? []),
              { url: item.url, kind: 'image', name: 'resized.png', id: item.id },
            ],
          });
          setEditorMode(null);
        } catch (err) {
          console.error('[OutputNodeView] resize bake failed:', err);
          setCommitError(err instanceof Error ? err.message : 'Resize failed');
        } finally {
          setPixCommitting(false);
        }
      })();
    },
    [editSourceUrl, canvasId, id, images, patchData],
  );
```

`handleCommit`（裁剪）整个替换为：

```tsx
  const handleCommit = useCallback(
    async (region: CropRegion) => {
      if (!canvasId || !editSourceUrl) return;
      try {
        setCommitting(true);
        setCommitError(null);
        const derived = await deriveCanvasCrop(canvasId, editSourceUrl, region, {
          nodeId: id,
        });
        // Replace THE edited image (primary, one grid item, or a history
        // item) with its cropped copy — a durable generated-media url, no
        // session token.
        patchData(swapEditedImage({ preview_url, images }, editSourceUrl, derived));
        setEditorOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to derive crop';
        setCommitError(message);
        // Leave the modal open so the user can retry or cancel.
      } finally {
        setCommitting(false);
      }
    },
    [canvasId, editSourceUrl, id, preview_url, images, patchData],
  );
```

`handleOutpaintCommit` 整个替换为：

```tsx
  const handleOutpaintCommit = useCallback(
    async (padding: OutpaintPadding, prompt: string) => {
      if (!canvasId || !editSourceUrl) return;
      try {
        setOutpaintCommitting(true);
        setOutpaintError(null);
        const derived = await deriveCanvasOutpaint(canvasId, editSourceUrl, padding, {
          nodeId: id,
          prompt: prompt || undefined,
        });
        // Spawn the extended image as a fresh node beside the source.
        const store = useCanvasCoreStore.getState();
        const self = store.nodes.find(
          (node) => (node as { id?: string }).id === id,
        ) as { position?: { x: number; y: number } } | undefined;
        const base = self?.position ?? { x: 0, y: 0 };
        const extendedNode = createOutputNode(
          { kind: 'image', preview_text: '', preview_url: derived.url },
          {
            position: {
              x: base.x + SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X,
              y: base.y,
            },
          },
        );
        store.setNodes([...store.nodes, extendedNode]);
        // IC 扩图联动: pre-seed a wired prompt with IC's own instruction.
        const extId = String((extendedNode as unknown as { id?: unknown }).id ?? '');
        if (extId) {
          createPromptFromNode(extId, {
            body: 'Remove the white area and fill the scene naturally',
            gen: { kind: 'image', model: '', ratio: 'auto', count: 1 },
            source_ref: derived.url,
          });
        }
        setOutpaintOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to extend canvas';
        setOutpaintError(message);
      } finally {
        setOutpaintCommitting(false);
      }
    },
    [canvasId, editSourceUrl, id],
  );
```

`handleGridCommit` 整个替换为：

```tsx
  const handleGridCommit = useCallback(
    async (lines: GridLines) => {
      if (!canvasId || !editSourceUrl) return;
      try {
        setGridCommitting(true);
        setGridError(null);
        const tiles = await deriveCanvasGrid(canvasId, editSourceUrl, lines, {
          nodeId: id,
        });
        // One output node per tile, mirroring the grid to the right of the
        // source node. The source node itself is left untouched.
        const store = useCanvasCoreStore.getState();
        const self = store.nodes.find(
          (node) => (node as { id?: string }).id === id,
        ) as { position?: { x: number; y: number } } | undefined;
        const base = self?.position ?? { x: 0, y: 0 };
        const startX = base.x + SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X;
        const tileNodes = tiles.map((tile) =>
          createOutputNode(
            { kind: 'image', preview_text: '', preview_url: tile.url },
            {
              position: {
                x:
                  startX +
                  (tile.col ?? 0) * (SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X),
                y: base.y + (tile.row ?? 0) * TILE_LAYOUT_STEP_Y,
              },
            },
          ),
        );
        store.setNodes([...store.nodes, ...tileNodes]);
        setGridOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to split image';
        setGridError(message);
      } finally {
        setGridCommitting(false);
      }
    },
    [canvasId, editSourceUrl, id],
  );
```

`handleMaskCommit` 的 `importCanvasMedia(file, canvasId, id, 'mask')` 不变。

- [ ] **Step 6: 改组件——工具栏、徽章、编辑器、灯箱**

工具栏（`OutputNodeToolbar` 的 props）：

```tsx
          onCrop={canDerive ? openEditor : undefined}
          onExpand={canDerive ? openOutpaintEditor : undefined}
          onMask={canCrop ? openMaskEditor : undefined}
```

以及 `onSplit={canDerive ? openGridEditor : undefined}`；`onUpscale` / `onDuplicate` 保持 `canCrop`。`openEditor` 的门控也改为 `canDerive`（函数体与依赖数组）。

删除 `Saved` 徽章块：

```tsx
          {resource_id && (
            <div className="text-[10px] uppercase tracking-wider text-emerald-500">
              Saved
            </div>
          )}
```

`UnifiedImageEditor` 的 props：

```tsx
          src={editSourceUrl ?? ''}
          onCropCommit={canDerive ? handleCommit : undefined}
          onOutpaintCommit={canDerive ? handleOutpaintCommit : undefined}
          onMaskCommit={canCrop ? handleMaskCommit : undefined}
          onSplitCommit={canDerive ? handleGridCommit : undefined}
```

外层条件 `{(primaryImageUrl || editingUrl) && (` 改为 `{editSourceUrl && (`。

灯箱 `editActions`：

```tsx
              : {
                  ...(canDerive
                    ? { crop: openEditor, expand: openOutpaintEditor, split: openGridEditor }
                    : {}),
                  ...(canCrop ? { mask: openMaskEditor } : {}),
                }
```

复核（应无输出）：

```bash
grep -n "resource_id\|canSplit\|buildPreviewUrl\|ensureResourceId\|getSupabaseClient\|getResourceFileUrl\|deriveCrop\b\|deriveGrid\b\|deriveOutpaint\b\|deriveMaskCutout" features/canvas-core/smart/nodes/OutputNodeView.tsx
```

- [ ] **Step 7: 跑测试 + 类型 + lint**

Run:

```bash
npx vitest run features/canvas-core/smart/swapEditedImage.test.ts features/canvas-core/smart/nodes/
npx tsc --noEmit -p .
npx eslint features/canvas-core/smart/swapEditedImage.ts features/canvas-core/smart/swapEditedImage.test.ts features/canvas-core/smart/nodes/OutputNodeView.tsx features/canvas-core/smart/nodes/OutputNodeView.*.test.tsx
```

Expected: `smart/nodes/` 下全部测试 PASS（含未改动的 asAsset / aspect / images / lightbox / recover / toolbar），tsc 与 eslint 无新增错误。（`OutputNodeView.toolbar.test.tsx` 直接渲染工具栏组件并自己传回调，不经过门控，无需改动。）

- [ ] **Step 8: 提交 + 开 PR4**

```bash
W=/Volumes/program/project-code/repos/nous-app-wt-derive-frontend
git -C $W add frontend/features/canvas-core/smart/swapEditedImage.ts frontend/features/canvas-core/smart/swapEditedImage.test.ts frontend/features/canvas-core/smart/nodes/OutputNodeView.tsx frontend/features/canvas-core/smart/nodes/OutputNodeView.derive.test.tsx frontend/features/canvas-core/smart/nodes/OutputNodeView.grid.test.tsx frontend/features/canvas-core/smart/nodes/OutputNodeView.outpaint.test.tsx frontend/features/canvas-core/smart/nodes/OutputNodeView.mask.test.tsx frontend/features/canvas-core/smart/nodes/OutputNodeView.promote.test.tsx
git -C $W commit -m "feat(canvas): 输出卡编辑器按正在编辑的图派生——不再自动入库，网格第 N 张图编辑的就是它"
git -C $W push -u origin feat/canvas-editors-take-reference
gh pr create --repo iocrazy/nous-app --base master --head feat/canvas-editors-take-reference \
  --title "feat(canvas): 画布编辑器接受任意图片引用（裁剪/扩图/切图不再要求先入库）" \
  --body "计划：docs/superpowers/plans/2026-09-10-canvas-derive-any-reference.md（Task 10–11）。删除打开编辑器即自动 promote；编辑按正在编辑的那张图的 URL 派生，产物为画布 scope 的生成内容；修掉网格第 2 张图裁剪/缩放实际作用在第 1 张、遮罩按钮无 resource_id 时点不开、节点数据里写入会话 token 三个缺陷。依赖 #<PR2 编号> 已上线。"
```

CI 绿即合并；`deploy-pages.yml` 跑完后执行 Task 12。

---

## Task 12: 真栈验收（PR4 上线后，PR5 之前）

"测试绿 ≠ 真栈正常"（CLAUDE.md 前端验收纪律）。本 Task 不改代码，结论写进 PR4 的评论。

- [ ] **Step 1: 确认前端已上线**

```bash
gh pr view <PR4 编号> --repo iocrazy/nous-app --json mergeCommit --jq .mergeCommit.oid
curl -sS https://app.nous.ink/version.json
```

Expected: `commitSha` 等于 PR4 的 merge commit。

- [ ] **Step 2: 生产走查脚本**

```bash
cd frontend && npm run e2e:prod
```

Expected: 全绿（它不覆盖编辑器，只证明核心路径没被带坏）。

- [ ] **Step 3: 浏览器真点一遍**（Claude in Chrome；登录态由用户提供）

在一张**团队项目**画布上依次做，每步在 Network 面板确认 `POST /api/v1/canvases/<id>/derive-*` 返回 200、画布上出现新图：

1. 生成的输出卡 → Crop → Apply（原地替换）。
2. 同一卡 → Expand → 拖右边 → Apply（旁边出现新卡 + 预填提示词卡）。
3. 同一卡 → Split → 2×2 → Apply（右侧出现 4 张卡）。
4. 多图输出卡双击第 2 张 → Crop → Apply（只替换第 2 张）。
5. 从 Library 面板拖进来的资源库图片（媒体卡）→ 编辑 → Crop → Apply（卡片追加一张）。
6. 同一媒体卡 → Resize → Apply。
7. 历史卡（只有 images 没有 preview_url）→ 双击 → Crop → Apply。

- [ ] **Step 4: 数据库核对**（gpupc；PG 容器内端口 55434）

先核列名（CLAUDE.md「Schema 迁移 / 代码漂移检查口径」）：

```bash
ssh gpupc "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \"SELECT column_name FROM information_schema.columns WHERE table_name='resources' AND column_name IN ('source_type','created_at')\""
```

再跑（`<canvas_id>` / `<team_id>` 换成走查用的画布与其项目 team）：

```sql
-- 1) 派生产物：role=derived、落在团队 scope、来源是规范标签不是 URL
SELECT id, scope_id, canvas_id, derivation_kind, params->>'role' AS role,
       params->>'derived_from' AS derived_from, review_state
FROM generated_media
WHERE canvas_id = <canvas_id> AND created_at > now() - interval '1 hour'
ORDER BY created_at DESC;
-- 期望：crop/outpaint/grid 行 role=derived、scope_id=<team_id>、derived_from 形如 gen:123 / resource:456；
--      resize 行 role=derived；遮罩/画笔行 role=mask/brush；全部 scope_id=<team_id>

-- 2) 走查期间没有新建 derived resources（不再"先入库"）
SELECT count(*) FROM resources
WHERE source_type = 'derived' AND created_at > now() - interval '1 hour';
-- 期望：0

-- 3) 画布数据里没有会话 token
SELECT id FROM canvases
WHERE id = <canvas_id> AND nodes_json::text LIKE '%token=eyJ%';
-- 期望：0 行

-- 4) 被编辑的源生成图没有因为"打开编辑器"被推进审阅状态
SELECT id, review_state, promoted_resource_id FROM generated_media
WHERE id IN (<走查里编辑过的源 generation id>);
-- 期望：review_state 仍是 unreviewed，promoted_resource_id 为空
```

- [ ] **Step 5: 引用跟着归档走**

在生成内容收件箱把走查画布上显示的一张图"入库"，回画布挪一下任意节点触发保存，然后：

```sql
SELECT r.node_id, r.resource_id, r.role
FROM canvas_resource_refs r
JOIN generated_media g ON g.promoted_resource_id = r.resource_id
WHERE r.canvas_id = <canvas_id> AND g.id = <刚入库的 generation id>;
```

Expected: 1 行，`role = output`。再保存一次画布，仍在（旧缺陷是第二次保存即消失）。打开该资源详情页，"Used in canvases" 列出这张画布。

- [ ] **Step 6: 错误漏斗**

```sql
SELECT module, message, COUNT(*) FROM application_logs
WHERE level = 'ERROR' AND logged_at >= now() - interval '1 hour'
  AND (module ILIKE '%canvas%' OR message ILIKE '%derive%' OR message ILIKE '%refs%')
GROUP BY module, message ORDER BY count DESC;
```

Expected: 无本次引入的新错误。任何一步不符合预期：停在这里修，不开 PR5。

---

## PR5 — `chore/remove-resource-derive-endpoints`

前置：Task 12 全部通过，且 PR4 上线**至少 24 小时**（给仍开着旧页面的标签页一个自然刷新窗口；`version.json` 轮询会提示刷新）。worktree：`git -C /Volumes/program/project-code/repos/nous-app worktree add /Volumes/program/project-code/repos/nous-app-wt-derive-cleanup -b chore/remove-resource-derive-endpoints origin/master`，然后 backend `uv sync`、frontend `npm ci`。

### Task 13: 删除旧的资源派生端点、客户端与自动 promote 桥

**Files:**
- Modify: `frontend/features/canvas-core/services/canvasService.ts`（删 `:142-304` 四节：`DerivedResource`、`DeriveCropOptions`、`deriveCrop`、`GridTileResult`、`GridDeriveResult`、`DeriveGridOptions`、`deriveGrid`、`DeriveMaskCutoutOptions`、`deriveMaskCutout`、`DeriveOutpaintOptions`、`deriveOutpaint`；行号以 grep 为准）
- Modify: `frontend/features/canvas-core/services/canvasService.test.ts`（删 `describe('deriveCrop'|'deriveGrid'|'deriveMaskCutout'|'deriveOutpaint')` 四块与对应 import）
- Modify: `frontend/features/canvas-core/smart/mediaEditBridge.ts`（删 `ensureResourceId`、`_resetPromoteCache` 与缓存；保留 `genIdFromDurableUrl`）
- Modify: `frontend/features/canvas-core/smart/mediaEditBridge.test.ts`（删 promote 相关用例与 `promoteGeneration` mock）
- Modify: `frontend/features/canvas-core/services/canvasGenerationService.ts:255`（`promoteGeneration` 仅当 grep 证明零调用方时删除）
- Modify: `frontend/features/canvas-core/smart/nodes/OutputNodeView.promote.test.tsx`（mock 里删 `ensureResourceId`，删 `not.toHaveBeenCalled` 那行与对应 import）
- Modify: `backend/app/api/resources_crud_router.py`（删 `:1391-1578` 四个端点与 `:36-39` 中不再使用的 schema import；`/{resource_id}/split` 不动）
- Modify: `backend/app/services/canvas/crop_derive_service.py` / `grid_derive_service.py` / `outpaint_derive_service.py`（删 `derive_*_resource(s)` 与只为它们服务的结果类型、文件名函数、`ResourcesRepository` / `load_source_image` / `persist_derived_image` import；保留 Task 1 的纯函数、`*DeriveError` 别名、`run_outpaint_via_nous`）
- Delete: `backend/app/services/canvas/mask_derive_service.py`、`backend/app/services/canvas/image_mask.py`、`backend/app/schemas/canvas_mask_schema.py`、`backend/app/schemas/canvas_grid_schema.py`、`backend/app/schemas/canvas_outpaint_schema.py`
- Modify: `backend/app/schemas/canvas_crop_schema.py`（删 `CropDeriveRequest` 及响应模型，保留 `CropRegionModel`）
- Delete: `backend/tests/test_crop_derive_service.py`、`test_grid_derive_service.py`、`test_outpaint_derive_service.py`、`test_mask_derive_service.py`、`test_image_mask.py`
- Modify: `backend/tests/test_derive_transforms.py`（先补上 AI 扩图成功路径，再删旧测试）

**Interfaces:**
- Consumes: 无新接口。
- Produces: 无。保留：`crop_image`、`GridTileImage`、`split_image_by_lines`、`extend_image`、`run_outpaint_via_nous`、`CropRegionModel`、`MAX_LINES_PER_AXIS`、`MAX_PAD_PER_SIDE`、`derive_persistence`（`/split` 在用）、`genIdFromDurableUrl`、`importResourceAsCanvasMedia`（Library 面板在用）。

- [ ] **Step 1: 删除闸门——证明零调用方**

```bash
cd /Volumes/program/project-code/repos/nous-app-wt-derive-cleanup
grep -rn "deriveCrop\b\|deriveGrid\b\|deriveMaskCutout\|deriveOutpaint\b\|ensureResourceId\|_resetPromoteCache" frontend --include='*.ts' --include='*.tsx' | grep -v node_modules | grep -v "services/canvasService\.\(ts\|test\.ts\)\|smart/mediaEditBridge\.\(ts\|test\.ts\)\|OutputNodeView\.promote\.test\.tsx"
grep -rn "derive-crop\|derive-grid\|derive-outpaint\|derive-mask-cutout" frontend admin browser backend/app --include='*.ts' --include='*.tsx' --include='*.py' | grep -v node_modules | grep -v "resources_crud_router.py\|canvas_derive_router.py"
grep -rn "promoteGeneration\b" frontend --include='*.ts' --include='*.tsx' | grep -v node_modules
```

Expected: 前两条**无输出**；第三条只剩 `canvasGenerationService.ts` 的定义与 `mediaEditBridge(.test).ts`——那就连 `promoteGeneration` 一起删；若还有别的调用方，保留它并在 PR 描述里写明。任一前两条有输出：停下，先迁移那个调用方。

生产侧再确认最近 7 天没人打旧端点——查 `api_request_logs` 表，**不要查容器日志**：`nous-backend` 的容器日志里不带请求行，`docker logs … | grep 'POST /api/v1/resources/…' | wc -l` 永远打印 `0`，那个闸门是探不到信号的假绿。

```bash
# Mac mini 上 gpupc 的 ssh 短名是 ubuntu（`ssh gpupc` 走的是另一个假 IP）；
# 换成你手上这台机器实际能连通的主机名即可。
ssh ubuntu "docker exec -i nous-db psql -U postgres -p 55434 -d postgres -tA" <<'SQL'
SELECT COUNT(*)
FROM api_request_logs
WHERE method = 'POST'
  AND path ~ '^/api/v1/resources/[^/]+/derive-'
  AND "timestamp" >= now() - interval '7 days';
SQL
```

`{resource_id}` 是字符串路径参数，正则用 `[^/]+` 而不是 `[0-9]+`，否则非数字 id 会漏计。

Expected: `0`。**同一条查询要带正向对照**——把 `resources` 那行换成新端点 `path ~ '^/api/v1/canvases/[^/]+/derive-'` 再跑一次，必须是正数；只有旧端点 0 而新端点也 0，说明是查询/表本身没数据，不构成"没人在调"的证据（2026-09-10 实测：旧 0、新 7）。非 0 就推迟 PR5，把 `COUNT(*)` 换成 `path, status_code, "timestamp"` 查是谁在调。

- [ ] **Step 2: 先补 AI 扩图成功路径测试（旧文件里唯一还没被 Task 1 覆盖的行为）**

`backend/tests/test_derive_transforms.py` 末尾追加：

```python
async def test_extend_image_ai_mode_uses_nous_bytes_when_available() -> None:
    ai_bytes = _png(7, 7)
    with patch(
        "app.services.canvas.outpaint_derive_service.run_outpaint_via_nous",
        new=AsyncMock(return_value=ai_bytes),
    ) as nous:
        out = await extend_image(
            _png(100, 50),
            "image/png",
            Padding(left=0.5, top=0.0, right=0.5, bottom=0.0),
            prompt="a windswept meadow",
            mode="ai",
            label="gen:5",
        )
    assert out == ai_bytes
    assert nous.await_args.kwargs["prompt"] == "a windswept meadow"


async def test_extend_image_deterministic_mode_never_calls_nous() -> None:
    with patch(
        "app.services.canvas.outpaint_derive_service.run_outpaint_via_nous",
        new=AsyncMock(),
    ) as nous:
        await extend_image(
            _png(100, 50),
            "image/png",
            Padding(left=0.5, top=0.0, right=0.5, bottom=0.0),
            prompt="ignored",
        )
    nous.assert_not_awaited()
```

Run: `cd backend && uv run pytest tests/test_derive_transforms.py -v`
Expected: PASS（行为已存在，这两条是把旧测试的覆盖搬过来）。

- [ ] **Step 3: 删后端**

按 Files 清单删除 / 修改。删完三个服务模块后各自只剩：

`crop_derive_service.py`：模块 docstring 改为 "Crop transform shared by the canvas derive path."，import 只剩 `DeriveError`、`CropRegion`、`crop_normalized`，内容只剩 `CropDeriveError = DeriveError` 与 `crop_image`。

`grid_derive_service.py`：import 只剩 `dataclass`、`Sequence`、`DeriveError`、`GridSplitError`、`tiles_from_lines`、`crop_normalized`；内容只剩 `GridDeriveError`、`GridTileImage`、`split_image_by_lines`。

`outpaint_derive_service.py`：删 `ResourcesRepository`、`ResourceRepoProtocol`、`load_source_image`、`persist_derived_image`、`Optional`、`OutpaintDeriveResult`、`_outpaint_filename`、`derive_outpaint_resource`；保留 `run_outpaint_via_nous`、`extend_image`、`_deterministic_fill`、`_fill_via_ai_or_fallback`。

`resources_crud_router.py`：删 `@router.post("/{resource_id}/derive-crop")`、`derive-grid`、`derive-mask-cutout`、`derive-outpaint` 四个端点函数整体；`:36-39` 四行 schema import 删除（先 `grep -n "CropDeriveRequest\|GridDeriveRequest\|MaskDeriveRequest\|OutpaintDeriveRequest" backend/app/api/resources_crud_router.py` 确认删完端点后已无引用）。

Run:

```bash
cd backend
uv run python -c "from app.main import app; print(sorted(r.path for r in app.routes if 'derive' in r.path or r.path.endswith('/split')))"
grep -rn "mask_derive_service\|image_mask\|canvas_mask_schema\|canvas_grid_schema\|canvas_outpaint_schema\|derive_crop_resource\|derive_grid_resources\|derive_outpaint_resource" app tests scripts
uv run pytest -q --ignore=tests/api --deselect tests/test_storage_migration_web_resource_files.py
```

Expected: 路由只剩三个 `/api/v1/canvases/{canvas_id}/derive-*` 与 `/api/v1/resources/{resource_id}/split`；grep 无输出；pytest 全绿。

- [ ] **Step 4: 删前端**

按 Files 清单删除。`mediaEditBridge.ts` 删完后只剩文件头注释（改写为只描述 `genIdFromDurableUrl`）与 `genIdFromDurableUrl`；`mediaEditBridge.test.ts` 只保留 `genIdFromDurableUrl` 的用例。

Run（在 `frontend/`）：

```bash
npx vitest run features/canvas-core/
npx tsc --noEmit -p .
npx eslint features/canvas-core/services/ features/canvas-core/smart/mediaEditBridge.ts features/canvas-core/smart/mediaEditBridge.test.ts features/canvas-core/smart/nodes/OutputNodeView.promote.test.tsx
```

Expected: 全绿，tsc / eslint 无新增错误。

- [ ] **Step 5: 格式化 + 提交 + PR**

```bash
cd backend && uv run black app/api/resources_crud_router.py app/services/canvas/ app/schemas/canvas_crop_schema.py tests/test_derive_transforms.py && uv run isort app/api/resources_crud_router.py app/services/canvas/ app/schemas/canvas_crop_schema.py tests/test_derive_transforms.py && uv run ruff check app tests
W=/Volumes/program/project-code/repos/nous-app-wt-derive-cleanup
git -C $W add -A backend frontend
git -C $W status --short
git -C $W commit -m "chore(canvas): 删除旧的资源派生端点、客户端与编辑器自动入库桥"
git -C $W push -u origin chore/remove-resource-derive-endpoints
gh pr create --repo iocrazy/nous-app --base master --head chore/remove-resource-derive-endpoints \
  --title "chore(canvas): 删除 /resources/{id}/derive-* 与自动 promote 桥" \
  --body "计划：docs/superpowers/plans/2026-09-10-canvas-derive-any-reference.md（Task 13）。闸门：前端 grep 零调用方；生产 24h 日志零请求（附命令输出）；Task 12 真栈验收已通过（见 #<PR4 编号> 评论）。/resources/{id}/split 未动。"
```

`git status --short` 核对只有清单内的文件。CI 绿即合并，部署后探 `readyz`，并在画布上再点一次 Crop 确认仍走 `/canvases/{id}/derive-crop`。

---

## 执行备注

- 每个 Task 结束都有独立可测的交付物；Subagent 执行时一个 Task 一个子代理，PR 粒度的合并 / 部署 / 真栈验收由主会话做。
- PR2 → PR4 之间是"后端已上线、前端未切换"的窗口，线上行为不变；PR4 → PR5 之间新旧端点并存，两套都能用。
- 本计划不含数据迁移：存量节点上的 `resource_id` / `crop_region` 保持原样（D7），被自动 promote 过的存量 resources 不清理（不在范围）。
