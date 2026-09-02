# 画布流畅感 Wave 1+2 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 画布节点永不加载原图（服务端 1024px WebP 预览，按需生成写回）；平移缩放无 transition 且触控板双指=平移；拖一个节点不重渲染其它节点，交互期间不模糊不写 store；图落地零布局跳动。全部可量化验收。

**Architecture:** 后端在 `/cover` 加预览分支（键约定 `<key>.preview.webp`，按需生成、失败回退原图、`?full=1` 吐原图）；前端 `fullResSrc()` 给灯箱/编辑器，节点保持 `mediaSrc()`；`CanvasEngine` 改非受控 viewport + `panOnScroll`；`toReactFlowNodes` 按引用缓存 + 节点视图 `memo`；交互期 body class 关 blur/阴影/transition；autosave 只在 dragEnd/moveEnd。

**Tech Stack:** FastAPI + Pillow(WebP) + 对象存储 `ObjectStore.exists/get_bytes/put_bytes`（`app/services/library/media_storage.py`）；React 19 + `@xyflow/react` 12.10 + zustand；vitest / pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-canvas-fluency-design.md`（§3 设计、§4 验收、§5 决策）

## Global Constraints

- **不改交互语义**：左键拖节点/平移的现有分配、快捷键、连线手势全部不动（Wave 3 另立）。`onlyRenderVisibleElements` **保留**。
- **`/cover` 无鉴权吐原图是今天的姿态**；`?full=1` 只是把它显式化，**不得**让预览分支引入任何新的鉴权口子或 500：预览生成任何一步失败 → 回退原图 + log，绝不 raise。
- 预览规格：最长边 **1024px**、`WEBP quality=82 method=4`；键 `= 原键 + ".preview.webp"`（同桶）；**不加表不加列不加迁移**。
- 后端 lint **black / isort / flake8（无 ruff）**；不用 `text()` 裸 SQL；子进程（若有）走 `safe_popen_kwargs`（本期无需子进程，Pillow 纯内存）。
- 前端：UI 文本英文；语义色 token；`vitest` 结论只认 `Tests N passed` 行；tsc 基线 56 不得增长。
- 每个任务的关键断言**看着它红过**；性能类任务用**渲染计数 spy** 做可证伪断言，不用「感觉更快」。
- commit `git -c user.email=ezufofoti59@gmail.com -c user.name=heygo`；分支 `feat/canvas-fluency-w1w2` 已从 `origin/master` 建；**不要 stash/pop**。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `backend/app/services/library/media_preview.py` | `preview_key()`、`render_preview_webp(bytes)->bytes`、`ensure_preview(row)->bytes|None` | 新建 |
| `backend/app/api/generated_media_router.py` | `/cover` 预览分支 + `?full=1` | 修改 |
| `backend/tests/services/library/test_media_preview.py` / `backend/tests/test_generated_media_cover.py` | | 新建 |
| `frontend/features/canvas-core/smart/mediaUrl.ts` | `fullResSrc()` | 修改 |
| `frontend/features/canvas-core/smart/nodes/OutputLightbox.tsx`、`editor/PaintCanvas.tsx`、`editor/UnifiedImageEditor.tsx`、`smart/mediaEditBridge.ts` | 原图消费方切 `fullResSrc` | 修改 |
| `frontend/index.css` | 删 viewport transition；`.mh-canvas-interacting` 规则 | 修改 |
| `frontend/canvas-kit/CanvasEngine.tsx` | `panOnScroll`/`zoomOnPinch`/`zoomOnScroll={false}`；非受控 viewport + `onMoveStart/End`；interacting class | 修改 |
| `frontend/features/canvas-core/ui/CanvasSurface.tsx` | `toReactFlowNodes` 引用缓存；moveEnd 持久化；去掉每帧 viewport 写 | 修改 |
| `frontend/features/canvas-core/store/canvasCoreStore.ts` | `setNodesDragTick` 不 dirty；`setViewportSettled` 替代每帧写 | 修改 |
| `frontend/features/canvas-core/smart/nodes/registry.ts` + `PromptNodeView.tsx` | `memo` 包装；去重复订阅；派生改选择器 | 修改 |
| `frontend/features/canvas-core/smart/nodes/OutputNodeToolbar.tsx`、`GroupNodeToolbar.tsx` | 按需挂载 | 修改 |
| `frontend/features/canvas-core/smart/nodes/OutputNodeView.tsx` | `<img loading decoding>` + `aspect-ratio` 来自 `gen.ratio` | 修改 |

---

### Task 1: 后端预览层 —— `media_preview.py` + `/cover` 分支

**Files:**
- Create: `backend/app/services/library/media_preview.py`
- Modify: `backend/app/api/generated_media_router.py:105-121`（`get_generation_cover`）
- Test: `backend/tests/services/library/test_media_preview.py`、`backend/tests/test_generated_media_cover.py`

**Interfaces:**
- Produces:
  ```python
  PREVIEW_MAX_EDGE = 1024; PREVIEW_SUFFIX = ".preview.webp"
  def preview_key(key: str) -> str
  def render_preview_webp(data: bytes) -> bytes            # 纯函数,Pillow,失败 raise
  async def ensure_preview(row: dict, *, store_factory=None) -> bytes | None
      # 对象存储行:exists→get 或 生成→put_bytes→返回;任何失败→None(调用方回退原图)
      # 文件系统行:本期返回 None(dev 无对象存储→原图,诚实回退)
  ```
  `/cover`：`full=1` → 原图；否则 `ensure_preview()` 非 None → `Response(webp, media_type="image/webp")`，None → 原图。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/services/library/test_media_preview.py
"""The preview tier: the canvas must never paint an original.

Measured 2026-09-02 on the user's 24-node canvas: 12 images, 15 MB, each
~1.3 MB PNG at 1672x941, served by /cover which claims to be a thumbnail and
is not. Every zoom re-uploads ~75 MB of decoded bitmap to the GPU.

Rules pinned here: 1024px max edge, WebP, key = original + ".preview.webp",
generated on first request and written back, and — the one that matters most
— any failure degrades to the original (today's behaviour), never to a 500.
"""
import io, struct, zlib
import pytest
from PIL import Image
from app.services.library.media_preview import (
    PREVIEW_MAX_EDGE, PREVIEW_SUFFIX, ensure_preview, preview_key, render_preview_webp,
)


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO(); Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG"); return buf.getvalue()


def test_preview_key_is_sibling_of_original():
    assert preview_key("t1/ab/cd/abcdef") == "t1/ab/cd/abcdef" + PREVIEW_SUFFIX


def test_render_scales_longest_edge_to_1024_and_emits_webp():
    out = render_preview_webp(_png(1672, 941))
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP"
    assert max(img.size) == PREVIEW_MAX_EDGE
    assert abs(img.size[0] / img.size[1] - 1672 / 941) < 0.01   # aspect preserved


def test_render_does_not_upscale_small_images():
    out = render_preview_webp(_png(640, 480))
    assert Image.open(io.BytesIO(out)).size == (640, 480)


def test_render_is_much_smaller_than_the_original():
    src = _png(1672, 941)
    assert len(render_preview_webp(src)) < len(src) / 4


class _FakeStore:
    def __init__(self, objects: dict[str, bytes], *, fail_put=False):
        self.objects, self.fail_put, self.puts = objects, fail_put, []
    async def exists(self, key): return key in self.objects
    async def get_bytes(self, key): return self.objects[key]
    async def put_bytes(self, key, data, **kw):
        if self.fail_put: raise RuntimeError("object store down")
        self.objects[key] = data; self.puts.append(key)


@pytest.mark.asyncio
async def test_ensure_preview_generates_writes_back_and_returns():
    store = _FakeStore({"t1/a/b/orig": _png(1672, 941)})
    row = {"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"}
    out = await ensure_preview(row, store_factory=lambda bucket: store)
    assert out is not None and Image.open(io.BytesIO(out)).format == "WEBP"
    assert store.puts == ["t1/a/b/orig" + PREVIEW_SUFFIX]      # written back once


@pytest.mark.asyncio
async def test_ensure_preview_serves_existing_without_regenerating():
    existing = render_preview_webp(_png(800, 600))
    store = _FakeStore({"t1/a/b/orig": _png(1672, 941), "t1/a/b/orig" + PREVIEW_SUFFIX: existing})
    out = await ensure_preview({"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"}, store_factory=lambda b: store)
    assert out == existing and store.puts == []


@pytest.mark.asyncio
async def test_ensure_preview_returns_none_when_write_back_fails():
    # The caller falls back to the original — a failed preview must never 500.
    store = _FakeStore({"t1/a/b/orig": _png(1672, 941)}, fail_put=True)
    out = await ensure_preview({"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"}, store_factory=lambda b: store)
    assert out is None


@pytest.mark.asyncio
async def test_ensure_preview_returns_none_for_corrupt_original():
    store = _FakeStore({"t1/a/b/orig": b"not a png"})
    assert await ensure_preview({"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"}, store_factory=lambda b: store) is None


@pytest.mark.asyncio
async def test_ensure_preview_returns_none_for_filesystem_rows_this_release():
    assert await ensure_preview({"file_path": "generated/x.png", "mime": "image/png"}) is None
```

```python
# backend/tests/test_generated_media_cover.py
"""/cover serves the preview; ?full=1 serves the original; failure degrades."""
from unittest.mock import AsyncMock, patch
import pytest
# 照仓内 test_generation_capabilities_endpoint.py 的方式:直接调 router 函数或 ASGI 客户端 —— 执行者按现状选一种并在报告写明


@pytest.mark.asyncio
async def test_cover_serves_webp_preview_when_available():
    ...  # patch GeneratedMediaRepository.get_by_id → image row; patch ensure_preview → b"RIFF....WEBP"; assert media_type image/webp + body


@pytest.mark.asyncio
async def test_cover_full_flag_serves_original_bytes():
    ...  # ensure_preview must NOT be called; _serve_media_row path taken


@pytest.mark.asyncio
async def test_cover_falls_back_to_original_when_preview_is_none():
    ...  # ensure_preview → None; response is the original (media_type image/png), status 200


@pytest.mark.asyncio
async def test_cover_keeps_immutable_cache_header_on_both_branches():
    ...
```

⚠️ 第二个文件的 4 处 `...` 必须补全为真实测试体（鉴权/调用形状按仓内现有 endpoint 测试抄），**不得留省略号**。

- [ ] **Step 2: RED** — `cd backend && uv run pytest tests/services/library/test_media_preview.py tests/test_generated_media_cover.py -v` → `ModuleNotFoundError` / 无 `full` 参数。

- [ ] **Step 3: 实现**

```python
# backend/app/services/library/media_preview.py
"""Canvas preview tier for generated media.

/cover used to hand the browser the ORIGINAL (1.3 MB PNGs on a 24-node canvas
= 15 MB, ~75 MB decoded). The canvas never needs more than ~1024px; the
lightbox/editor fetch the original explicitly (?full=1).

Previews are derived by key convention (original + ".preview.webp", same
bucket), generated on first request and written back. No table, no column.
Every failure returns None so the caller can serve the original — today's
behaviour — instead of a 500.
"""
from __future__ import annotations
import io
from typing import Any, Callable, Optional
from loguru import logger
from app.services.library.media_storage import ObjectStore, resolve_media_source

PREVIEW_MAX_EDGE = 1024
PREVIEW_SUFFIX = ".preview.webp"
_WEBP_QUALITY = 82


def preview_key(key: str) -> str:
    return f"{key}{PREVIEW_SUFFIX}"


def render_preview_webp(data: bytes) -> bytes:
    """Longest edge → PREVIEW_MAX_EDGE (never upscale), WebP. Raises on bad input."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        w, h = img.size
        scale = PREVIEW_MAX_EDGE / max(w, h)
        if scale < 1:
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "WEBP", quality=_WEBP_QUALITY, method=4)
        return out.getvalue()


async def ensure_preview(
    row: dict[str, Any], *, store_factory: Optional[Callable[[str], Any]] = None
) -> Optional[bytes]:
    """Preview bytes for an object-store image row, generating + writing back
    on first use. None on ANY failure or for filesystem rows (caller serves
    the original)."""
    loc = resolve_media_source(str(row.get("file_path") or ""))
    if not loc.is_object_store:
        return None
    store = (store_factory or ObjectStore)(loc.bucket)
    pkey = preview_key(loc.key)
    try:
        if await store.exists(pkey):
            return await store.get_bytes(pkey)
        original = await store.get_bytes(loc.key)
        preview = render_preview_webp(original)
        await store.put_bytes(pkey, preview, content_type="image/webp")
        return preview
    except Exception as exc:
        logger.warning("[preview] falling back to original for {}: {}", loc.key, exc)
        return None
```

⚠️ `put_bytes` 的关键字参数名（`content_type` / `mime`）以 `media_storage.py` 实际签名为准——执行者先读再写，别照抄。

`/cover`：

```python
@router.get("/{gen_id}/cover")
async def get_generation_cover(gen_id: int, full: int = 0):
    row = await GeneratedMediaRepository().get_by_id(gen_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row.get("media_kind") != "image":
        raise HTTPException(status_code=404, detail="no cover")
    headers = {"Cache-Control": "public, max-age=604800, immutable"}
    if not full:
        preview = await ensure_preview(row)
        if preview is not None:
            return Response(content=preview, media_type="image/webp", headers=headers)
    return await _serve_media_row(row, headers=headers)
```

docstring 改成事实：默认预览（1024 WebP，按需生成），`?full=1` 原图，两者同样无鉴权（与此前 `/cover` 吐原图的姿态一致）。

- [ ] **Step 4: GREEN + 突变** — 把 `ensure_preview` 的 `except Exception` 分支改为 `raise`，`..._returns_none_when_write_back_fails` 与 `..._corrupt_original` 转红；还原再绿。贴输出。
- [ ] **Step 5**: `uv run pytest tests/services/library tests/test_generated_media_cover.py tests -k "generated_media" -q`；全量；lint。Commit：

```bash
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(media): 生成图预览层 —— /cover 默认 1024px WebP(按需生成写回,失败回退原图),?full=1 吐原图"
```

---

### Task 2: 前端原图消费方切 `fullResSrc`

**Files:**
- Modify: `frontend/features/canvas-core/smart/mediaUrl.ts`（加 `fullResSrc`）
- Modify: `OutputLightbox.tsx:579,602,623,630`、`editor/PaintCanvas.tsx`（`fetchBaseBitmap` 与显示 `<img>`）、`editor/UnifiedImageEditor.tsx`、`smart/mediaEditBridge.ts`（导出/编辑取原图处）
- Test: `frontend/features/canvas-core/smart/mediaUrl.test.ts`（新建或追加）、既有灯箱/编辑器测试跟改

**Interfaces:** `fullResSrc(url) => string`：对匹配 `/api/v1/generated-media/{id}/cover` 的 URL 追加 `?full=1`（已有 query 则 `&full=1`），其它 URL 原样经 `mediaSrc`。

- [ ] **Step 1: 写失败测试**

```ts
// mediaUrl.test.ts
import { describe, expect, it, vi } from 'vitest';
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
import { fullResSrc, mediaSrc } from './mediaUrl';

describe('fullResSrc', () => {
  it('asks /cover for the original explicitly', () => {
    expect(fullResSrc('/api/v1/generated-media/7/cover')).toBe('https://api.test/api/v1/generated-media/7/cover?full=1');
  });
  it('appends with & when a query already exists', () => {
    expect(fullResSrc('/api/v1/generated-media/7/cover?v=2')).toMatch(/\?v=2&full=1$/);
  });
  it('leaves non-cover urls exactly as mediaSrc does', () => {
    for (const u of ['/api/v1/generated-media/7/file', 'https://x/y.png', '']) expect(fullResSrc(u)).toBe(mediaSrc(u));
  });
});
```

灯箱测试：断言主图与 compare 图的 `src` 以 `?full=1` 结尾；`PaintCanvas.export.test.tsx`：断言 `apiFetch` 被以带 `?full=1` 的路径调用（P4 前是 `/cover`）；节点视图测试：断言 `OutputNodeView` 的 `<img src>` **不含** `full=1`（节点永远预览）。

- [ ] **Step 2: RED** — 相应断言失败。
- [ ] **Step 3: 实现** `fullResSrc`；四个消费方切换；`mediaEditBridge.ts` 的 `DURABLE_RE` 已能容纳 query（它匹配路径），确认后不动。
- [ ] **Step 4: GREEN + 突变** — 把灯箱主图改回 `mediaSrc`，灯箱断言转红；还原。`npx vitest run features/canvas-core/smart/nodes features/canvas-core/editor` 全绿；tsc 基线不变。
- [ ] **Step 5: Commit** `feat(canvas): 灯箱/编辑器/画笔取原图(?full=1),节点永远用预览`

---

### Task 3: 平移缩放 —— 去 transition、触控板平移、非受控 viewport

**Files:**
- Modify: `frontend/index.css:1549-1554`（删 transition 块）
- Modify: `frontend/canvas-kit/CanvasEngine.tsx:753-800`（props）与 viewport 受控逻辑（:777, :544-549）
- Modify: `frontend/features/canvas-core/ui/CanvasSurface.tsx:106, 418-432, 616`；`store/canvasCoreStore.ts:975-985`
- Modify: `ui/TopNodeBar.tsx:80`（改用 RF `useViewport()`）
- Test: `frontend/canvas-kit/CanvasEngine.props.test.tsx`（新建）、`canvasCoreStore.viewport.test.ts`（新建）

**Interfaces:** `CanvasEngine` 接受 `defaultViewport` + `onMoveStart/onMoveEnd`（透传 RF），不再接受 `viewport`；store 新增 `setViewportSettled(viewport)`（= 写 store + `markDirty` 一次）；删除 `setViewportOnMove`/`flushViewportDirty`。

- [ ] **Step 1: 写失败测试**

```tsx
// CanvasEngine.props.test.tsx —— 用 vi.mock('@xyflow/react') 捕获 <ReactFlow> 收到的 props
it('pans on trackpad scroll and zooms on pinch / modifier+wheel', () => {
  const props = renderEngineAndCaptureReactFlowProps();
  expect(props.panOnScroll).toBe(true);
  expect(props.zoomOnScroll).toBe(false);
  expect(props.zoomOnPinch).toBe(true);
});
it('does not control the viewport per frame', () => {
  const props = renderEngineAndCaptureReactFlowProps();
  expect(props.viewport).toBeUndefined();
  expect(props.defaultViewport).toEqual({ x: 0, y: 0, zoom: 1 });   // from store
  expect(typeof props.onMoveEnd).toBe('function');
});
```

```ts
// canvasCoreStore.viewport.test.ts
it('a settled viewport bumps revision exactly once', () => { ... setViewportSettled({x:1,y:2,zoom:1.5}); expect(revision).toBe(before+1); expect(scheduleSave).toHaveBeenCalledTimes(1) });
it('has no per-frame viewport writer any more', () => { expect((useCanvasCoreStore.getState() as any).setViewportOnMove).toBeUndefined(); });
```

```ts
// index.css 断言(读文件):
it('the viewport layer carries no transition', () => { expect(readFileSync('index.css','utf8')).not.toMatch(/\.react-flow__viewport\s*{[^}]*transition/s); });
```

- [ ] **Step 2: RED**。
- [ ] **Step 3: 实现**：删 CSS 块；`CanvasEngine`：`panOnScroll zoomOnPinch zoomOnScroll={false}`（`panOnDrag={[0,1]}` **不动**），`defaultViewport={initialViewport}`，透传 `onMoveStart`/`onMoveEnd`；`CanvasSurface`：`onMoveEnd={(_, vp) => setViewportSettled(vp)}`，删除 RAF 每帧写；`TopNodeBar` 用 `useViewport()`；画布**切换**时（`canvasId` 变）通过 `useReactFlow().setViewport(store.viewport)` 一次性应用持久化视口（`CanvasPage.tsx:517-545` 的 fit/heal 逻辑照旧）。
- [ ] **Step 4: GREEN + 突变**：把 `panOnScroll` 删掉，props 测试红；还原。`npx vitest run canvas-kit features/canvas-core` 全绿。
- [ ] **Step 5: Commit** `perf(canvas): 视口去 transition、非受控 + moveEnd 持久化;触控板双指=平移,捏合/⌘滚轮=缩放`

---

### Task 4: 渲染成本 —— 稳定引用、memo、去重复订阅、选择器化

**Files:**
- Modify: `ui/CanvasSurface.tsx:55-88, 187-194`（`toReactFlowNodes` 引用缓存）
- Modify: `smart/nodes/registry.ts:15-32`（`memo` 包装）
- Modify: `smart/nodes/PromptNodeView.tsx:224-255`（去 :226 重复订阅；派生改选择器）
- Modify: `smart/promptInputs.ts:59-64`、`smart/chainRun.ts:103-133`（接受预建索引，避免每次 `new Map(nodes)`）
- Test: `ui/CanvasSurface.rerender.test.tsx`（新建，渲染计数 spy）

**Interfaces:** `toReactFlowNodes(nodes, selectionSet)` 用 `WeakMap<CanvasNode, RFNode>` + 上次 selected 状态缓存：节点对象引用未变且 selected 未变 → 返回同一 RF 节点对象。

- [ ] **Step 1: 写失败测试**

```tsx
// CanvasSurface.rerender.test.tsx
/** Dragging one node must not render the others. Before: every store change
 *  rebuilt the whole RF node array, so React Flow's reference check failed and
 *  every node view re-rendered on every drag tick. */
it('a drag tick on node A renders A only', () => {
  const counts = installRenderCounters();          // wraps registry views with a counting HOC via vi.mock
  renderSurfaceWith(threePromptNodes);
  counts.reset();
  act(() => useCanvasCoreStore.getState().setNodesDragTick(moveNode('p1', { x: 10, y: 10 })));
  expect(counts.of('p1')).toBe(1);
  expect(counts.of('p2')).toBe(0);
  expect(counts.of('p3')).toBe(0);
});
it('unchanged nodes keep their React Flow object identity across a store update', () => {
  const a = toReactFlowNodes(nodes, sel); const b = toReactFlowNodes([...nodes], sel);  // same node refs, new array
  expect(b[1]).toBe(a[1]);
});
```

- [ ] **Step 2: RED**（今天 p2/p3 各渲染 1 次，且引用不同）。
- [ ] **Step 3: 实现**：`WeakMap` 缓存；`registry.ts` 每个视图 `memo(View)`；`PromptNodeView` 删 :226；`resolveSourceUrls`/`isChainTail` 改为接收 `useCanvasCoreStore(useShallow(s => indexFor(s)))` 提供的、按 `nodes`/`connections` 引用记忆化的索引（`byId: Map`, `incoming: Map<id, edge[]>`），而不是每个节点各建一次。
- [ ] **Step 4: GREEN + 突变**：删掉 `memo` 包装，渲染计数测试红；还原。`npx vitest run features/canvas-core` 全绿。
- [ ] **Step 5: Commit** `perf(canvas): 拖一个节点不再重渲染其它节点 —— RF 节点引用缓存 + 视图 memo + 派生索引共享`

---

### Task 5: 交互期间关掉模糊/阴影/transition；工具条按需挂载

**Files:**
- Modify: `frontend/index.css:1218-1226, 1387-1394`（`.mh-canvas-interacting` 规则）
- Modify: `frontend/canvas-kit/CanvasEngine.tsx`（`onNodeDragStart/Stop`、`onMoveStart/End` 处 toggle body class）
- Modify: `smart/nodes/OutputNodeToolbar.tsx:86-90`、`GroupNodeToolbar.tsx:46`（`pinned || hovered` 才渲染）
- Test: `canvas-kit/CanvasEngine.interacting.test.tsx`、`OutputNodeToolbar.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
it('body carries mh-canvas-interacting from drag start to drag stop', () => {
  const props = renderEngineAndCaptureReactFlowProps();
  act(() => props.onNodeDragStart?.(evt, node));
  expect(document.body.classList.contains('mh-canvas-interacting')).toBe(true);
  act(() => props.onNodeDragStop?.(evt, node));
  expect(document.body.classList.contains('mh-canvas-interacting')).toBe(false);
});
it('…and from move start to move end', /* 同上,onMoveStart/onMoveEnd */);
it('clears the class on unmount even mid-drag', /* 卸载后 class 不残留 */);
// CSS 断言:
it('interacting state disables backdrop blur and transitions on node cards', () => {
  const css = readFileSync('index.css','utf8');
  expect(css).toMatch(/\.mh-canvas-interacting \.mh-node[^}]*backdrop-filter:\s*none/s);
  expect(css).toMatch(/\.mh-canvas-interacting \.mh-node[^}]*transition:\s*none/s);
});
// 工具条:
it('the toolbar is not in the DOM until hovered or pinned', () => {
  render(<OutputNodeToolbar pinned={false} hovered={false} … />); expect(screen.queryByTestId('output-toolbar')).toBeNull();
  rerender(hovered); expect(screen.getByTestId('output-toolbar')).toBeInTheDocument();
});
```

- [ ] **Step 2: RED** → **Step 3: 实现**（class 在 `useEffect` 清理里也移除；hover 状态由节点根 `onMouseEnter/Leave` 提供，通过既有 props 通道传给工具条）→ **Step 4: GREEN + 突变**（删 body class toggle，测试红）→ **Step 5: Commit** `perf(canvas): 拖拽/平移期间关闭卡片模糊与阴影;工具条按需挂载`

---

### Task 6: 自动保存只在交互结束

**Files:**
- Modify: `store/canvasCoreStore.ts:956-962`（`setNodesDragTick` 不 `markDirty`）；确认 Task 3 已删每帧 viewport dirty
- Test: `canvasCoreStore.autosave.test.ts`（新建）

- [ ] **Step 1: 写失败测试**

```ts
it('drag ticks never bump revision or arm a save; drag end does exactly once', () => {
  const save = spyScheduleSave();
  const r0 = revision();
  for (let i = 0; i < 10; i++) setNodesDragTick(moved(i));
  expect(revision()).toBe(r0); expect(save).toHaveBeenCalledTimes(0);
  setNodes(finalNodes);                      // drag end path
  expect(revision()).toBe(r0 + 1); expect(save).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: RED**（今天每 tick +1）→ **Step 3**：删 `setNodesDragTick` 里的 `markDirty()`；注释说明 drag end 的 `setNodes` 负责 → **Step 4: GREEN + 突变**（加回 markDirty 转红）→ **Step 5: Commit** `perf(canvas): 拖拽 tick 不再触发保存与 revision;只在 drag end 落一次`

---

### Task 7: 图片与占位零布局跳动

**Files:**
- Modify: `smart/nodes/OutputNodeView.tsx:620-677`（`<img loading="lazy" decoding="async">`；格子容器 `style={{ aspectRatio }}`）
- Modify: `smart/genSlots.ts`（占位格携带 `gen_ratio` 以便未落地时也能定形）——或直接从 prompt 节点的 `gen.ratio` 读（执行者按数据可达性选，报告写明）
- Test: `OutputNodeView.aspect.test.tsx`（新建）

- [ ] **Step 1: 写失败测试**

```tsx
it('shimmer cells and landed images share the aspect-ratio of the requested ratio', () => {
  renderOutput({ gen_pending: 1, images: [{url:'/api/v1/generated-media/1/cover', kind:'image'}] }, promptWith({ ratio: '16:9' }));
  const cells = screen.getAllByTestId('output-cell');
  for (const c of cells) expect(c.style.aspectRatio).toBe('16 / 9');
});
it('falls back to 1 / 1 when no ratio is known', …);
it('images are lazy and async-decoded', () => { const img = screen.getByRole('img'); expect(img.getAttribute('loading')).toBe('lazy'); expect(img.getAttribute('decoding')).toBe('async'); });
```

- [ ] **Step 2: RED** → **Step 3: 实现** → **Step 4: GREEN + 突变**（删 `aspectRatio` 样式转红）→ **Step 5: Commit** `perf(canvas): 占位与图片按 gen.ratio 定固有尺寸,零布局跳动;img lazy+async decode`

---

### Task 8: 全量回归 + 真栈量化验收

- [ ] **Step 1**：`cd frontend && npx vitest run`（只认 `Tests N passed` + exit 0）；`npx tsc --noEmit`（56）；`cd backend && uv run pytest -q` + 三 lint。
- [ ] **Step 2**（合并部署后，控制器做）：对画布 `337004651010097` 重跑载荷测量：

```bash
# 与 2026-09-02 基线同一脚本(12 张 /cover 总字节),预期 15 MB → < 2.5 MB,且 Content-Type image/webp
# ?full=1 的字节数应与基线原图一致
```

- [ ] **Step 3**：`cd frontend && npm run e2e:prod` 走查；用户真机点开同一张画布对比拖拽/缩放手感（主观，但载荷与重渲染两项已可量化）。
- [ ] **Step 4**：PR 正文引用 spec §4 六条验收，附前后载荷数字。

---

## Self-Review

**Spec 覆盖**：§3.1 预览层 → T1 ✅；§3.2 前端取图 → T2/T7 ✅；§3.3 平移缩放 → T3 ✅；§3.4 渲染成本（引用缓存/memo/选择器/交互期 class/工具条）→ T4/T5 ✅；§3.5 autosave → T3（viewport）+ T6（drag）✅；§4 验收 1–6 各有对应测试或测量 ✅；§2 非目标未混入 ✅。
**占位扫描**：T1 第二个测试文件 4 处 `...` 与 T5/T7 的 `/* 同上 */` 简写——执行者必须补全；已在文中标注。
**类型一致性**：`ensure_preview(row, *, store_factory)` 与 `/cover` 调用一致；`fullResSrc` 只在 T2 定义、T2 消费；`setViewportSettled` 在 T3 定义、T3/T6 引用；body class 名 `mh-canvas-interacting` 在 T5 的 CSS 与引擎两侧一致。
**已知风险**：非受控 viewport 后，画布切换/fitView/heal 路径（`CanvasPage.tsx:517-545`）必须改走 `setViewport()`——T3 步骤已点名；`toReactFlowNodes` 的 WeakMap 键是节点对象，`patchNode` 产生新对象即失效，符合预期（只有变的节点重建）。
