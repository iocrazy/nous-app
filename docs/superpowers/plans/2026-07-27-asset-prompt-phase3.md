# Asset Prompt Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 Phase 2 终审记账的四个 deferred 项：① Library 参考图真正接入 i2i 管线（durable URL）② MediaNode 缩略图点开 OutputLightbox ③ picker 过滤口径与 `hasPromptData` 统一 ④ Send to Canvas 过滤 classic 画布。

**Architecture:** ① 加后端端点 `POST /generated-media/import-from-resource`——服务端从 `resources.file_path` 直接 `register_generated_media(source_path=...)` 铸 durable URL（不走客户端下载再上传）；前端在 Library pick / Send to Canvas 消费点先铸 URL 再建 MediaNode，失败降级回 cover URL（仅视觉）。② `MediaNodeView` 按 `OutputNodeView` 的模式接 `OutputLightbox`。③ picker 改 4 列 `.or()` + 客户端 `hasPromptData` 复筛（PostgREST 难表达 trim/空串组合）。④ 一行过滤。

**Tech Stack:** FastAPI（1 个新端点）+ React 19 + vitest。

**Ledger 出处:** `.superpowers/sdd/progress.md` 的 "DEFERRED to Phase 3" 段（主仓库）。
**分支/工作区:** `feature/asset-prompt-phase3` @ `.worktrees/asset-prompt-p3`（已建，基于 master `ccbdb75`）。

## Global Constraints

- 只在 `feature/asset-prompt-phase3` 分支、`.worktrees/asset-prompt-p3` 工作区内工作；绝不切分支、绝不碰主仓库目录
- UI 文本英文 + i18n；后端提交前跑 `uv run black/isort/flake8`（CI 有这三关——Phase 1 曾在这栽过两次）
- 前端基线：typecheck **62** errors == master 基线（接触文件必须零错误）；vitest 全量 3187 是 master 现状
- 已核实的关键事实（直接引用，不必再探）：
  - `register_generated_media(*, user_id, scope_id, source_url=None, source_path=None, mime, origin)`（`backend/app/services/library/generated_media_service.py:234`），`source_path` 收本地文件路径
  - `resources.file_path` 列存在；权限检查用 `check_media_access(resource_id, user_id, None)`（`resources_ai_router.py` 同款）；scope 用 `generated_media_router.py:78` 的 `_scope(auth)`
  - `importCanvasMedia`（`frontend/features/canvas-core/smart/mediaImport.ts:21`）是 File 上传版，本次不动它，新加平行函数
  - `OutputLightbox({items: LightboxItem[]={url,name?}, index, kind: 'image'|'video', ...})`（`OutputLightbox.tsx:30`）；接入范式抄 `OutputNodeView.tsx:89-106,365`
  - i2i 判定：`promptInputs.ts` `DURABLE_PREFIX='/api/v1/generated-media/'`——URL 换成 durable 后 `resolveSourceUrl` 自动生效，生成侧零改动
- 每 task 一 commit + trailer `Claude-Session: https://claude.ai/code/session_017MjbKQdeuX9c91L5xdhbns`

---

### Task 1: spec §3 口径修订（文档）

**Files:**
- Modify: `docs/superpowers/specs/2026-07-26-asset-prompt-management-design.md` §3「素材 Prompt 库」

- [ ] **Step 1**: 把 §3 的过滤定义改为与 `hasPromptData` 一致的口径：

> 过滤条件：**四列（gen_prompt / gen_prompt_zh / gen_prompt_negative / gen_prompt_negative_zh）任一非空（trim 后非空串）**。实现分两层：Supabase 查询用 4 列 `.or(not.is.null)` 粗筛，客户端用 `hasPromptData` 精筛（PostgREST 不便表达 trim/空串组合）。负面-only 素材因此也进 Library；被清空成 `''` 的不再误列。

同段落追加一行修订记录（日期 + "Phase 3 统一，原口径只查正向两列"）。

- [ ] **Step 2**: Commit `docs(specs): Prompt 库过滤口径统一为 hasPromptData 四列语义 (Phase 3)`

---

### Task 2: 后端 import-from-resource 端点（TDD）

**Files:**
- Modify: `backend/app/api/generated_media_router.py`
- Test: `backend/tests/test_generated_media_import_from_resource.py`（新建）

**Interfaces:**
- Produces: `POST /api/v1/generated-media/import-from-resource`，body `{"resource_id": "<snowflake str>"}`，响应 `{data: {id, url, media_kind, mime}}`（与 `/import` 同构）。403 无权限、404 资源不存在/无 file_path、400 非 image/video mime。

- [ ] **Step 1: 写失败测试**（mock 层级参照 `tests/test_upload_postprocess_workflow.py` 的 repo-mock 风格；router 函数直接 import 调用、mock `ResourcesRepository.get_resource_by_id` / `check_media_access` / `register_generated_media`，不起 TestClient——参照 `build_translate_plan` 纯函数测试的轻量哲学，把可测逻辑拆成模块级 helper）：

```python
# backend/tests/test_generated_media_import_from_resource.py
"""Unit tests for resolve_resource_import — resource → register args."""

import pytest

from app.api.generated_media_router import ResourceImportError, resolve_resource_import


def test_resolves_image_resource():
    row = {"id": 1, "file_path": "/data/uploads/a.png", "mime_type": "image/png"}
    args = resolve_resource_import(row)
    assert args == {"source_path": "/data/uploads/a.png", "mime": "image/png"}


def test_missing_file_path_raises_404():
    with pytest.raises(ResourceImportError) as e:
        resolve_resource_import({"id": 1, "file_path": None, "mime_type": "image/png"})
    assert e.value.status_code == 404


def test_non_media_mime_raises_400():
    with pytest.raises(ResourceImportError) as e:
        resolve_resource_import(
            {"id": 1, "file_path": "/d/a.pdf", "mime_type": "application/pdf"}
        )
    assert e.value.status_code == 400


def test_video_mime_allowed():
    row = {"id": 1, "file_path": "/d/v.mp4", "mime_type": "video/mp4"}
    assert resolve_resource_import(row)["mime"] == "video/mp4"
```

- [ ] **Step 2**: 跑 `cd backend && uv run pytest tests/test_generated_media_import_from_resource.py -v` → ImportError FAIL

- [ ] **Step 3: 实现**（`generated_media_router.py`）：

```python
class ResourceImportError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def resolve_resource_import(resource: dict) -> dict:
    """Map a resources row to register_generated_media source args."""
    file_path = (resource.get("file_path") or "").strip()
    if not file_path:
        raise ResourceImportError(404, "Resource has no local file")
    mime = (resource.get("mime_type") or "").lower()
    if not (mime.startswith("image/") or mime.startswith("video/")):
        raise ResourceImportError(400, "only image/* or video/* resources")
    return {"source_path": file_path, "mime": mime}
```

端点（放在 `/import` 之后；request body 用内联 Pydantic model 或 `Body(...)`，风格照本文件其它端点）：

```python
@router.post("/import-from-resource")
async def import_from_resource(payload: ResourceImportRequest, auth: AuthDep) -> dict:
    """Mint a durable /generated-media/ URL from an existing library resource.

    The canvas i2i bridge only reads durable generated-media URLs
    (promptInputs.ts DURABLE_PREFIX), so loading a library asset as an i2i
    reference requires re-registering its file server-side — no client
    download/upload round-trip.
    """
    from app.api.media_permissions import check_media_access
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.library.generated_media_service import (
        GenerationOrigin,
        register_generated_media,
    )

    resource = await ResourcesRepository().get_resource_by_id(payload.resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(payload.resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        args = resolve_resource_import(resource)
    except ResourceImportError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    row = await register_generated_media(
        user_id=auth.user_id,
        scope_id=await _scope(auth),
        source_path=args["source_path"],
        mime=args["mime"],
        origin=GenerationOrigin.IMPORT,
    )
    return {"data": {"id": row["id"], "url": row["url"], "media_kind": row.get("media_kind"), "mime": args["mime"]}}
```

（`GenerationOrigin.IMPORT` 的枚举名先读 `generated_media_service.py` 确认——`/import` 端点用什么这里就用什么；`row` 的 url 字段名同样以 `/import` 的响应组装为准。`ResourceImportRequest(BaseModel): resource_id: str`。）

- [ ] **Step 4**: 测试 PASS + `uv run black --check`/`isort --check-only`/`flake8` 对两个触碰文件全过
- [ ] **Step 5**: Commit `feat(api): generated-media 支持从库资源铸 durable URL (import-from-resource)`

---

### Task 3: 前端 i2i 接通（durable URL 进 MediaNode）

**Files:**
- Modify: `frontend/features/canvas-core/smart/mediaImport.ts`（加 `importResourceAsCanvasMedia`）
- Modify: `frontend/features/canvas-core/smart/loadPromptAsset.ts`（`buildPromptAssetLoad` 加 `mediaUrl` 参数）
- Modify: `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx`（pick 时先铸 URL）
- Modify: `frontend/features/canvas-core/smart/CanvasComposer.tsx`（promptInsert 消费同款）
- Test: `frontend/features/canvas-core/smart/loadPromptAsset.test.ts`（改）+ `mediaImport.importResource.test.ts`（新建）

**Interfaces:**
- Produces:
  - `importResourceAsCanvasMedia(resourceId: string): Promise<{url: string; kind: 'image'|'video'}>`（`apiFetch` POST import-from-resource；照 `importCanvasMedia` 的响应解析/报错风格）
  - `buildPromptAssetLoad({asset, lang, promptNodeId, promptNodePosition, mediaUrl})`——新增必传 `mediaUrl: string`，media 节点 items 用它（调用方决定传 durable 或降级的 cover URL）

- [ ] **Step 1**: 失败测试：`buildPromptAssetLoad` 用传入 `mediaUrl`（不再内部调 `getResourceCoverUrl`）；`importResourceAsCanvasMedia` 解析 `{data:{url}}`、无 url 时 throw
- [ ] **Step 2**: 实现；两个消费点改为：

```typescript
let mediaUrl: string;
try {
  mediaUrl = (await importResourceAsCanvasMedia(asset.id)).url;
} catch (err) {
  console.error('[promptAsset] durable import failed, falling back to cover:', err);
  mediaUrl = getResourceCoverUrl(asset.id);   // visual-only fallback, no i2i
}
```

`PromptNodeView.handlePickAsset` 变 async（onPick 回调里 await）；CanvasComposer 的 promptInsert effect 里同样先铸再建节点（effect 内 async IIFE，`insertedRef` 已在铸造前 check-and-set 防重入——保持 guard 在最前）。`loadPromptAsset.ts` 头注释更新：durable URL 时 i2i 生效，cover 降级仅视觉。
- [ ] **Step 3**: 全量 smart 测试 + typecheck 62 → Commit `feat(canvas): Library/SendToCanvas 参考图铸 durable URL — i2i 真正接通`

---

### Task 4: MediaNodeView 接 OutputLightbox

**Files:**
- Modify: `frontend/features/canvas-core/smart/nodes/MediaNodeView.tsx`
- Test: `frontend/features/canvas-core/smart/nodes/MediaNodeView.lightbox.test.tsx`（新建）

- [ ] **Step 1**: 失败测试（harness 抄 `MediaNodeView.test.tsx`）：点缩略图 → lightbox 出现（portal 到 body、显示大图 url）；带多 items 时 index 对应点击项；关闭回调工作
- [ ] **Step 2**: 实现照 `OutputNodeView.tsx:89-106` 范式：`const [lightboxIndex, setLightboxIndex] = useState<number|null>(null)`；items（仅 `kind==='image'` 参与图片 lightbox；video 项用 `kind:'video'` 打开）→ `LightboxItem[]`；缩略图 `<img>` 包 button `onClick={() => setLightboxIndex(i)}`（`nodrag` class）；`{lightboxIndex !== null && <OutputLightbox items={...} index={lightboxIndex} kind={...} onClose={() => setLightboxIndex(null)} ...其余必传 props 照 OutputNodeView 的传法 />}`
- [ ] **Step 3**: 测试 PASS + 既有 MediaNodeView 测试无回归 + typecheck → Commit `feat(canvas): MediaNode 缩略图点开 OutputLightbox 大图 (spec §7.1 补齐)`

---

### Task 5: picker 口径统一 + classic 过滤

**Files:**
- Modify: `frontend/services/resourceService.ts`（fetchPromptAssets）
- Modify: `frontend/components/resources/SendToCanvasModal.tsx`
- Test: `frontend/services/resourceService.promptAssets.test.ts`（改）+ `frontend/components/resources/SendToCanvasModal.test.tsx`（改）

- [ ] **Step 1**: 失败测试：(a) fetchPromptAssets 的 `.or()` 含四列 `not.is.null`；(b) 返回行里全空/空串的行被客户端滤掉（mock 返回一条 `{gen_prompt: '', ...全空}` + 一条 negative-only → 只留后者）；(c) SendToCanvasModal 画布列表不含 `kind==='classic'`
- [ ] **Step 2**: 实现：
  - `.or('gen_prompt.not.is.null,gen_prompt_zh.not.is.null,gen_prompt_negative.not.is.null,gen_prompt_negative_zh.not.is.null')` + `.limit((opts.limit ?? 50) + 30)`（超采）→ map 后 `.filter(hasPromptData).slice(0, opts.limit ?? 50)`（import `hasPromptData`，`PromptAsset` 字段名与其入参兼容——不兼容就加个字段适配）
  - SendToCanvasModal：`canvases.filter((c) => c.kind !== 'classic')`
- [ ] **Step 3**: 测试 PASS + typecheck → Commit `fix(fe): Prompt 库过滤对齐 hasPromptData 四列口径;SendToCanvas 过滤 classic 画布`

---

### Task 6: 全量验证 + 交付

- [ ] `cd backend && uv run pytest tests/test_generated_media_import_from_resource.py tests/services/test_png_prompt_extractor.py -q` 全过；black/isort/flake8 对后端触碰文件全过
- [ ] `cd frontend && npx vitest run` 全量（基线 3187+新增）；`npm run typecheck` == 62；`npm run lint` 0 errors
- [ ] ledger 记录；push 分支 + 开 PR（base master；⚠️ 托管 CI 需要仓库 public 或 billing 已修——PR 描述里注明；后端改动会触发 Deploy GPU self-hosted 不受影响）

## Self-Review 记录

- 四个 deferred 项 ↔ Task 映射：①=T2+T3、②=T4、③=T1+T5、④=T5。
- 类型一致性：`importResourceAsCanvasMedia` 返回 `{url, kind}`（T3 产/消）；`buildPromptAssetLoad.mediaUrl` 必传（T3 改签名，两个调用方同 task 内更新，测试同步改）。
- 占位符：T2 的 `GenerationOrigin` 枚举名/响应 url 字段名standing指向 `/import` 现有实现为准——判定路径明确。
