# 出图生成请求契约 — P4（UI 按能力渲染）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** UI 只展示当前模型做得到的旋钮，做不到的不再是假开关；后端丢弃的旋钮（`dropped_knobs`）在 prompt 节点上可见——「你选的 quality 被忽略了」从库里的一行 jsonb 变成用户看得见的一句话。

**Architecture:** 后端新增只读端点 `GET /api/v1/canvases/generation-capabilities`（按目录模型名返回该行 protocol 的 `ProviderCapabilities` 投影，服务端算好，前端不需要知道 `actual_provider`）。前端一个模块级缓存 hook `useModelCapabilities`，`GenFooterControls` 据此过滤比例网格、隐藏 quality/resolution pill；`PromptNodeView` 的负向框按能力隐藏（当前全 provider 为 ✗ → 全隐藏）；`generationRunner` 轮询终态时从 `task.metadata.dropped_knobs` 把丢弃清单 patch 回节点 data，`PromptNodeView` 在 `RunStatusBadge` 旁渲染「Ignored: quality」徽章。

**Tech Stack:** FastAPI（一个只读端点）· React 19 / TypeScript / vitest。后端 `cd backend && uv run pytest`，前端 `cd frontend && npx vitest run`。

**Spec:** `docs/superpowers/specs/2026-08-29-generation-request-contract-design.md` §3.2（UI 隐藏 + dropped 可见）、§9 已裁决：dropped 提示放 prompt 节点徽章旁；capabilities 独立端点。

## Global Constraints

- **P1/P2 已上线，不要重做**：`ProviderCapabilities` 在每个 protocol 上（`resolve_generation_protocol(key).capabilities`）；`dropped_knobs` 已进 `task_tracking.metadata` 并随 `GET /canvases/generations/{task_id}` 的 `metadata` 到达前端。
- 后端：无 schema 变更；无 `text()` 裸 SQL；lint 门禁 **black / isort / flake8（无 ruff）**。
- 前端：**UI 文本一律英文**（Title Case 按钮/标签）；文案走 i18n（en/zh 两个文件都要加 key）；**禁 emoji**；状态色用语义 token（ok/warn/danger/info），不用旧色相类名。
- capabilities 未下发（老后端 / 请求失败）时 UI **按「全支持」渲染** = 今天的行为，绝不能变差（spec §5）。
- 隐藏是「不渲染」，**不是 disabled 灰掉**——灰掉仍是承诺（spec §8 决策 2）。
- 前端测试跟改规则：断言可见性；`vitest --reporter=basic` 跑 0 个测试也 exit 0，结论只认 `Tests N passed` 行。
- commit 用 `git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "..."`；分支从 `origin/master` 建、`git switch -c`、不要 stash/pop。
- 每个任务的关键测试**看着它红过**；UI 隐藏类的突变验证 = 把能力硬编码回「全支持」，隐藏断言必须转红。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `backend/app/api/canvases_router.py` | `GET /canvases/generation-capabilities`：`{model_name: caps投影}` | 修改 |
| `backend/tests/test_generation_capabilities_endpoint.py` | | 新建 |
| `frontend/features/canvas-core/services/canvasGenerationService.ts` | `listGenerationCapabilities()` + `ModelCapabilities` 类型 | 修改 |
| `frontend/features/canvas-core/smart/nodes/useModelCapabilities.ts` | 模块缓存 hook（照 `useGenerationModels` 的模式） | 新建 |
| `frontend/features/canvas-core/smart/nodes/GenFooterControls.tsx` | 比例网格按 `caps.ratios` 过滤；quality/resolution pill 按能力隐藏 | 修改 |
| `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx` | 负向框按能力隐藏；`RunStatusBadge` 旁渲染 dropped 徽章 | 修改 |
| `frontend/features/canvas-core/smart/generationRunner.ts` | 终态时把 `task.metadata.dropped_knobs` patch 回节点 | 修改 |
| `frontend/features/canvas-core/smart/types.ts` | 节点 data 加 `last_dropped?: string[]` | 修改 |
| `frontend/public/locales/{en,zh}.json` | `canvas.ignoredKnobs` 等 key | 修改 |
| 对应 `.test.tsx` / `.test.ts` | | 新建/修改 |

---

### Task 1: 后端 capabilities 端点

**Files:**
- Modify: `backend/app/api/canvases_router.py`（紧挨 `list_generation_models`，同一鉴权/可见性口径）
- Test: `backend/tests/test_generation_capabilities_endpoint.py`

**Interfaces:**
- Produces: `GET /api/v1/canvases/generation-capabilities` →
  ```json
  {"success": true, "data": {"codex-local-image": {"ratios": ["1:1","2:3",...], "quality": false, "resolution": false, "max_refs": 9, "negative": false, "video_modes": []}, ...}}
  ```
  key 是目录行 `name`（前端已用它标识模型）；`ratios` 排序稳定（沿 `ASPECT_RATIOS` 的声明序）；`honours_ratio` **不下发**——它是内部实现细节，UI 用不到，少给一个就少一个漂移面。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_generation_capabilities_endpoint.py
"""The capabilities endpoint: the UI's licence to hide a knob.

Server-side projection keyed by catalog row NAME, so the frontend never
learns `actual_provider` and never re-implements the registry lookup —
one predicate, one place (the same rule as the owner-scope fix in P1).
Visibility must match `generation-models` exactly: a model the picker
shows must have a caps entry, and a hidden model must not leak its caps.
"""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

_ROWS = [
    {"name": "codex-local-image", "type": "image", "actual_provider": "codex-local"},
    {"name": "mediahub-doubao-seedream-t2i", "type": "image", "actual_provider": "doubao"},
    {"name": "some-chat-model", "type": "llm", "actual_provider": "deepseek"},
]


@pytest.fixture
def client(auth_override):  # reuse the repo's existing auth fixture pattern
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_returns_caps_keyed_by_catalog_name(client):
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository"
    ) as repo, patch(
        "app.services.ai.platform_model_visibility.filter_platform_models_for_user",
        new=AsyncMock(side_effect=lambda _u, rows: rows),
    ):
        repo.return_value.list_enabled = AsyncMock(return_value=_ROWS)
        resp = await client.get("/api/v1/canvases/generation-capabilities")

    data = resp.json()["data"]
    codex = data["codex-local-image"]
    assert codex["quality"] is False          # P1 flipped this to honest False
    assert codex["max_refs"] == 9
    assert len(codex["ratios"]) == 8
    ark = data["mediahub-doubao-seedream-t2i"]
    assert set(ark["ratios"]) == {"16:9", "9:16", "1:1", "4:3", "3:4"}
    assert ark["max_refs"] == 0


@pytest.mark.asyncio
async def test_non_generation_rows_are_absent(client):
    ...  # same patches; assert "some-chat-model" not in data


@pytest.mark.asyncio
async def test_unknown_provider_maps_to_restrictive_default_not_500(client):
    """A catalog row whose actual_provider has no protocol must not break the
    endpoint — it gets the none() projection (drops loudly downstream)."""
    ...  # row with actual_provider="ghost"; assert data["ghost-model"]["ratios"] == []


@pytest.mark.asyncio
async def test_visibility_filter_applies(client):
    """A model Settings hides must not leak its caps entry."""
    ...  # filter returns only the first row; assert doubao absent


@pytest.mark.asyncio
async def test_honours_ratio_is_not_exposed(client):
    ...  # assert "honours_ratio" not in data["codex-local-image"]
```

⚠️ 上面 4 处 `...` 是因为鉴权 fixture 与 patch 组合要照本仓 `tests/` 现有写法接（先 `grep -rn "ASGITransport\|auth_override" backend/tests | head` 找同类测试抄口径）。**执行者必须补全为真实测试体，不得留 `...`**；若仓里没有现成的 endpoint 测试模式，退一步直接单测 router 函数（绕过 HTTP 层），并在报告里写明选了哪种。

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/test_generation_capabilities_endpoint.py -v`
Expected: 404（端点不存在）或 AttributeError。

- [ ] **Step 3: 实现（放在 `list_generation_models` 之后）**

```python
@router.get("/canvases/generation-capabilities")
async def list_generation_capabilities(auth: AuthDep) -> dict:
    """Per-model knob capabilities, keyed by catalog row name.

    Server-side projection of each row's protocol capabilities, so the UI
    can hide what a model cannot honour without ever learning
    ``actual_provider`` or re-implementing the registry lookup. Visibility
    follows ``generation-models`` exactly (same repo call, same Settings
    filter) — a model the picker shows always has an entry here.

    ``honours_ratio`` is deliberately NOT exposed: it describes an internal
    strategy, not something the UI can act on.
    """
    from app.repositories import mediahub_model_repository as _repo_mod
    from app.services.ai.platform_model_visibility import (
        filter_platform_models_for_user,
    )
    from app.services.ai.provider_protocols import resolve_generation_protocol
    from app.services.ai.provider_protocols.base import ProviderCapabilities
    from app.services.generation.aspect import ASPECT_RATIOS

    rows = await _repo_mod.get_mediahub_model_repository().list_enabled(
        viewer_user_id=auth.user_id
    )
    rows = await filter_platform_models_for_user(auth.user_id, rows)

    order = list(ASPECT_RATIOS)  # stable declaration order for the UI grid
    data: dict[str, dict] = {}
    for r in rows:
        if r.get("type") not in ("image", "video"):
            continue
        proto = resolve_generation_protocol((r.get("actual_provider") or "").lower())
        caps = proto.capabilities if proto else ProviderCapabilities.none()
        data[str(r.get("name"))] = {
            "ratios": [x for x in order if x in caps.ratios],
            "quality": caps.quality,
            "resolution": caps.resolution,
            "max_refs": caps.max_refs,
            "negative": caps.negative,
            "video_modes": sorted(caps.video_modes),
        }
    return {"success": True, "data": data}
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && uv run pytest tests/test_generation_capabilities_endpoint.py tests/test_provider_capabilities.py -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/canvases_router.py backend/tests/test_generation_capabilities_endpoint.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): capabilities 只读端点 —— 按目录模型名下发能力投影,UI 隐藏假开关的依据"
```

---

### Task 2: 前端 service + `useModelCapabilities` hook

**Files:**
- Modify: `frontend/features/canvas-core/services/canvasGenerationService.ts`
- Create: `frontend/features/canvas-core/smart/nodes/useModelCapabilities.ts`
- Test: `frontend/features/canvas-core/smart/nodes/useModelCapabilities.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface ModelCapabilities {
    ratios: string[]; quality: boolean; resolution: boolean;
    max_refs: number; negative: boolean; video_modes: string[];
  }
  export async function listGenerationCapabilities(): Promise<Record<string, ModelCapabilities>>
  /** null = capabilities unknown (old backend / fetch failed) → callers render FULL support. */
  export function useModelCapabilities(model: string | null | undefined): ModelCapabilities | null
  export function _resetModelCapabilitiesCache(): void
  ```

- [ ] **Step 1: 写失败测试**

```ts
// frontend/features/canvas-core/smart/nodes/useModelCapabilities.test.ts
/**
 * Capabilities cache: the UI's licence to hide a knob.
 *
 * `null` is a deliberate value, not an error state: it means "capabilities
 * unknown" (old backend, fetch failed), and every consumer must render FULL
 * support in that case — hiding a knob on missing data would break working
 * setups on the day an old backend serves a new frontend.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const listGenerationCapabilities = vi.fn();
vi.mock('../../services/canvasGenerationService', async (orig) => ({
  ...(await orig<object>()),
  listGenerationCapabilities: () => listGenerationCapabilities(),
}));

import {
  _resetModelCapabilitiesCache,
  useModelCapabilities,
} from './useModelCapabilities';

const CAPS = {
  'codex-local-image': {
    ratios: ['1:1', '16:9'], quality: false, resolution: false,
    max_refs: 9, negative: false, video_modes: [],
  },
};

beforeEach(() => {
  _resetModelCapabilitiesCache();
  listGenerationCapabilities.mockReset();
});

describe('useModelCapabilities', () => {
  it('returns the caps for a known model', async () => {
    listGenerationCapabilities.mockResolvedValue(CAPS);
    const { result } = renderHook(() => useModelCapabilities('codex-local-image'));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current!.quality).toBe(false);
  });

  it('returns null while loading, on fetch failure, and for an unknown model', async () => {
    listGenerationCapabilities.mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useModelCapabilities('codex-local-image'));
    expect(result.current).toBeNull();
    await waitFor(() => expect(listGenerationCapabilities).toHaveBeenCalled());
    expect(result.current, 'failure must read as "unknown", never as "supports nothing"').toBeNull();
  });

  it('fetches once per session (module cache), like useGenerationModels', async () => {
    listGenerationCapabilities.mockResolvedValue(CAPS);
    const a = renderHook(() => useModelCapabilities('codex-local-image'));
    await waitFor(() => expect(a.result.current).not.toBeNull());
    renderHook(() => useModelCapabilities('codex-local-image'));
    expect(listGenerationCapabilities).toHaveBeenCalledTimes(1);
  });

  it('returns null for an empty/absent model name', () => {
    const { result } = renderHook(() => useModelCapabilities(''));
    expect(result.current).toBeNull();
  });
});
```

- [ ] **Step 2: RED** — `cd frontend && npx vitest run features/canvas-core/smart/nodes/useModelCapabilities.test.ts`，Expected: 模块不存在。

- [ ] **Step 3: 实现** — service 加 `listGenerationCapabilities()`（`GET /api/v1/canvases/generation-capabilities`，走既有 `apiFetch` 信封）；hook 照抄 `useGenerationModels.ts` 的模块缓存/inflight/失败降级模式（失败 → 缓存 `null` 并 console.error，**不是缓存空对象**——空对象会把每个模型都判成「无能力」）。

- [ ] **Step 4: GREEN** — 同命令，4 条全过。

- [ ] **Step 5: Commit**

```bash
git add frontend/features/canvas-core/services/canvasGenerationService.ts frontend/features/canvas-core/smart/nodes/useModelCapabilities.*
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): useModelCapabilities —— null=未知按全支持渲染,失败绝不读成'什么都不支持'"
```

---

### Task 3: `GenFooterControls` 按能力渲染

**Files:**
- Modify: `frontend/features/canvas-core/smart/nodes/GenFooterControls.tsx`
- Test: `frontend/features/canvas-core/smart/nodes/GenFooterControls.caps.test.tsx`（新建；既有 `GenFooterControls.test.tsx` 不动，它钉的是全支持形态）

**Interfaces:**
- Consumes: Task 2 的 `useModelCapabilities(gen.model)`
- Produces: props 不变；行为变化——`caps` 非 null 时：比例网格只列 `caps.ratios`（`auto` 档**始终保留**，它是前端概念）；`caps.quality === false` 时不渲染 `pill-quality`；`caps.resolution === false` 时不渲染分辨率列。`caps === null` 时与今天完全一致。

- [ ] **Step 1: 写失败测试**

```tsx
// GenFooterControls.caps.test.tsx
/**
 * The footer only offers what the model can honour.
 *
 * A pill for a knob the provider ignores is a fake switch — the user turns
 * it and nothing happens, silently. P1 made the backend drop-and-name those
 * knobs; this makes the UI stop offering them. Hiding, not disabling:
 * a greyed-out control still promises the capability exists.
 *
 * `caps === null` (unknown) must render EXACTLY today's full set — an old
 * backend serving a new frontend must not lose working controls.
 */
```

用例（照既有 `GenFooterControls.test.tsx` 的渲染 helper 与 testid 约定写）：
1. mock `useModelCapabilities` 返回 ark 形态（5 档、quality:false、resolution:false）→ 打开尺寸弹层，断言 `21:9` 选项**不可见**、`16:9` 可见、`Auto` 仍在第一位；`pill-quality` 不在文档中（`queryByTestId` 为 null）。
2. mock 返回 null → `pill-quality` 可见、比例网格含全部 8 档 + Auto（与今天一致）。
3. mock 返回 codex 形态（8 档、quality:false——P1 的诚实值）→ 8 档都在、`pill-quality` 不在。
4. 视频 kind 时 `caps.ratios` 同样过滤 aspect 网格（若视频与图片共用网格代码则一条断言即可，写明共用点）。

- [ ] **Step 2: RED** — 断言 1/3 的隐藏项失败（今天全渲染）。

- [ ] **Step 3: 实现** — 组件顶部 `const caps = useModelCapabilities(isImage ? gen.model : gen.model)`；过滤处：

```tsx
const offeredRatios = caps ? FOOTER_RATIOS.filter((r) => r === 'auto' || caps.ratios.includes(r)) : FOOTER_RATIOS;
```

quality pill 与 RESOLUTIONS 列各包一层 `(caps === null || caps.quality) && ...` / `(caps === null || caps.resolution) && ...`。**不要**动 `RATIO_LABELS` 本身。

- [ ] **Step 4: GREEN + 突变验证** — 把 `offeredRatios` 硬改回 `FOOTER_RATIOS`，断言 1 必须转红；还原再绿。贴输出。跑 `npx vitest run features/canvas-core/smart/nodes` 确认既有测试不回归。

- [ ] **Step 5: Commit**

```bash
git add frontend/features/canvas-core/smart/nodes/GenFooterControls*
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): footer 按模型能力渲染 —— ark 不再显示 21:9,codex 不再显示假 quality;caps 未知=今天原样"
```

---

### Task 4: 负向框按能力隐藏 + dropped 徽章

**Files:**
- Modify: `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx`（:659 负向框；:435 `RunStatusBadge` 旁）
- Modify: `frontend/features/canvas-core/smart/generationRunner.ts`（终态处读 `task.metadata.dropped_knobs`）
- Modify: `frontend/features/canvas-core/smart/types.ts`（`last_dropped?: string[]`）
- Modify: `frontend/public/locales/en.json` / `zh.json`
- Test: `frontend/features/canvas-core/smart/nodes/PromptNodeView.dropped.test.tsx` + `generationRunner` 既有测试文件加断言

**Interfaces:**
- Produces: 节点 data 的 `last_dropped?: string[]`；徽章文案 i18n key `canvas.ignoredKnobs`（en: `"Ignored: {{knobs}}"`，zh: `"已忽略：{{knobs}}"`）。

- [ ] **Step 1: 写失败测试**

```tsx
// PromptNodeView.dropped.test.tsx
/**
 * What the backend dropped, the user sees.
 *
 * P2 wrote dropped_knobs into task metadata; this repo has shipped the
 * "backend returns it, frontend never reads it" failure three times before
 * (attachment_failures, session freshness, the deleted endpoint). The badge
 * next to the run status is the consumer that closes the loop: dispatch is
 * where the dropping happened, so the run badge is where the answer lives.
 */
```

用例：
1. 节点 data 带 `last_dropped: ['quality']` → 文案 `Ignored: quality` 可见，紧邻 run status 徽章（同一容器）。
2. `last_dropped: []` 或 undefined → 徽章不渲染（`queryByText(/Ignored:/)` 为 null）。
3. 负向框：mock `useModelCapabilities` 返回 `negative:false` → `negative_body` 已有值的节点**仍显示**（不吞用户已写的内容），但 `negative_body === undefined` 的节点不再出现添加入口/框体；caps null → 与今天一致。
4. runner 测试（加在既有 `generationRunner` 测试文件里）：`pollGeneration` 返回 `{phase:'completed', metadata:{result_url:'/r', dropped_knobs:['quality']}}` → 断言 runner 以 `last_dropped:['quality']` patch 节点（用既有 onDispatched/patch 桩的同款接线）。

- [ ] **Step 2: RED**。

- [ ] **Step 3: 实现**
  - `generationRunner.ts` 在读 `task.metadata?.result_url` 的同一处读 `task.metadata?.dropped_knobs`，随现有节点回写通道 patch `last_dropped`（多 task 的 prompt 取并集）。
  - `PromptNodeView.tsx` :435 旁：

```tsx
{Array.isArray(last_dropped) && last_dropped.length > 0 && (
  <span className="rounded bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn" data-testid="dropped-knobs-badge">
    {t('canvas.ignoredKnobs', { knobs: last_dropped.join(', ') })}
  </span>
)}
```

  - 负向框 :659 的条件加一层 caps 判定：`negative_body !== undefined` 恒显示（不吞已有内容）；「添加负向框」的入口在 `caps && !caps.negative` 时不渲染。
  - i18n 两个文件加 key。

- [ ] **Step 4: GREEN + 突变验证** — 把 runner 里 `dropped_knobs` 的读取删掉，用例 4 与 1（经集成路径）转红；还原再绿。跑 `npx vitest run features/canvas-core/smart` 全绿。

- [ ] **Step 5: Commit**

```bash
git add frontend/features/canvas-core frontend/public/locales
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): dropped_knobs 用户可见 —— run 徽章旁'Ignored: …';负向框按能力隐藏但绝不吞已写内容"
```

---

### Task 5: 全量回归 + 收尾

- [ ] **Step 1**: `cd frontend && npx vitest run > /tmp/p4.log 2>&1; echo exit=$?`（只认 exit 码与 `Tests N passed` 行）；`npx tsc --noEmit -p tsconfig.json`（基线 ~56，不得新增）；`cd backend && uv run pytest -q` + 三个 lint。
- [ ] **Step 2**: e2e 烟测一条（可选，若 `frontend/e2e` 已有 footer 弹层用例则跟改断言而非新增）。
- [ ] **Step 3**: Commit + push + `gh pr create`（正文引用 spec §3.2 与本计划；截图留待真机）。

---

## Self-Review

**Spec 覆盖（P4 范围）**：§3.2 UI 隐藏 → T3 ✅；负向框下架（按能力，非删代码）→ T4 ✅；dropped 可见（§9 裁决：prompt 节点徽章旁）→ T4 ✅；capabilities 独立端点（§9 裁决）→ T1 ✅；§5 老后端兼容（caps 未知=全支持）→ T2/T3 每处都有 null 分支测试 ✅。
**占位扫描**：T1 有 4 处 `...`（鉴权 fixture 依仓内现状选型），已显式标注执行者必须补全。其余任务代码完整。
**类型一致性**：`ModelCapabilities` 六字段与后端投影一致（`honours_ratio` 双侧都不出现）✅；`last_dropped` 在 types.ts / runner / PromptNodeView 三处同名 ✅；`auto` 档只存在于前端 `FOOTER_RATIOS`，后端 `ratios` 不含它，T3 的过滤显式放行 ✅。
**已知风险**：负向框「已有内容恒显示」依赖 `negative_body !== undefined` 这个既有约定——T4 用例 3 双向钉住；`GenFooterControls` 既有全支持测试不动，靠 null 分支保证兼容。
