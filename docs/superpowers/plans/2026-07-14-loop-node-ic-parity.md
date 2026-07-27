# Loop Node IC-Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the smart-canvas Loop node to Infinite-Canvas parity — kill the dead `batch` run-mode, let a loop drive per-round image generation with batch-sliced source images, and rebuild the node UI as IC's card (segmented mode, image/prompt toggles, panels, counter chip, footer).

**Architecture:** Extend, don't rebuild. The image-generation pipeline (`withGenerationRunner`), the per-round output slot (`upsertRoundSlot` already consumes `result.urls`), the `media → loop → prompt` connection rules, and the loop cascade's per-round `RunnerContext` (already carries `gen`+`source_url`) all exist. Two PRs: **PR1** wires the caller to `withGenerationRunner` and adds batch-slicing (Tasks 1-4); **PR2** rewrites the node card UI (Tasks 5-6).

**Tech Stack:** React 19 + TypeScript, `@xyflow/react` (React Flow), Zustand store (`canvasCoreStore`, `loopRunStore`), Vitest + Testing Library, TailwindCSS + CSS custom props in `frontend/index.css`, i18next.

## Global Constraints

- **Worktree:** All work happens in `.worktrees/feature-loop-node-ic-parity` on branch `feature/loop-node-ic-parity` (off master). Run commands from `frontend/` inside that worktree.
- **UI language:** All user-facing strings via i18n with **English** keys/values; Chinese only as translated values in `zh.json`. Never hardcode Chinese in TSX. (CLAUDE.md UI rule.)
- **Immutability:** Never mutate node data in place — spread new objects (`{ ...ctx, ... }`).
- **Lint gate before commit:** `cd frontend && npm run lint` must pass (eslint react-hooks flat config).
- **Single `source_url`:** the dispatch takes one `source_url` (`generationRunner.ts:91`); `image_batch_size > 1` feeds only `window[0]`. Multi-ref i2i is out of scope.
- **Batch-mode removal is behavior-preserving:** legacy persisted `mode:'batch'` already runs serially (`loopRunner.ts:135` only branches `parallel`); the UI must render it as Serial.
- **Test runner:** `cd frontend && npx vitest run <path>` for a single file; `-t "<name>"` for one test.

---

## File Structure

**PR1 — logic**
- `frontend/features/canvas-core/smart/types.ts` — `LoopMode` (drop `batch`), `LoopNodeData` (+3 fields), `LOOP_MODE_TONE` (drop `batch`).
- `frontend/features/canvas-core/smart/factories.ts` — `createLoopNode` defaults.
- `frontend/features/canvas-core/smart/loopVars.ts` — `clampBatchSize`, `sliceLoopImages` (+ existing helpers).
- `frontend/features/canvas-core/smart/loopVars.test.ts` — unit tests for the two new helpers.
- `frontend/features/canvas-core/smart/loopRun.ts` — `resolveCaller` wraps `withGenerationRunner`.
- `frontend/features/canvas-core/smart/loopRun.gen.test.ts` — new: caller drives generation.
- `frontend/features/canvas-core/smart/loopRunner.ts` — batch-slice `source_url` per round.
- `frontend/features/canvas-core/smart/loopRunner.test.ts` — extend: per-round sliced source.

**PR2 — UI**
- `frontend/features/canvas-core/smart/nodes/LoopNodeView.tsx` — full card rewrite.
- `frontend/index.css` — `.mh-loop-*` classes (append near `.mh-node`, ~line 852).
- `frontend/public/locales/en.json` + `frontend/public/locales/zh.json` — loop keys.
- `frontend/features/canvas-core/smart/nodes/smart-node-edit.test.tsx` — toggles/fields.

---

# PR1 — Logic

### Task 1: Data model — drop `batch`, add image/prompt/batch fields

**Files:**
- Modify: `frontend/features/canvas-core/smart/types.ts` (`LoopMode` ~44, `LoopNodeData` ~190-205, `LOOP_MODE_TONE` ~327-331)
- Modify: `frontend/features/canvas-core/smart/factories.ts:196-202`
- Test: `frontend/features/canvas-core/smart/factories.test.ts`

**Interfaces:**
- Produces: `LoopMode = 'serial' | 'parallel'`; `LoopNodeData` with `image_input?: boolean`, `show_prompt?: boolean`, `image_batch_size?: number`; `createLoopNode` defaulting `show_prompt:true`, `image_input:false`, `image_batch_size:1`.

- [ ] **Step 1: Write the failing test** — append to `factories.test.ts`:

```ts
import { createLoopNode } from './factories';

describe('createLoopNode IC-parity defaults', () => {
  it('defaults show_prompt on, image_input off, batch 1, serial', () => {
    const node = createLoopNode();
    expect(node.data.mode).toBe('serial');
    expect(node.data.show_prompt).toBe(true);
    expect(node.data.image_input).toBe(false);
    expect(node.data.image_batch_size).toBe(1);
  });

  it('preserves caller overrides', () => {
    const node = createLoopNode({ image_input: true, image_batch_size: 3, show_prompt: false });
    expect(node.data.image_input).toBe(true);
    expect(node.data.image_batch_size).toBe(3);
    expect(node.data.show_prompt).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run features/canvas-core/smart/factories.test.ts -t "IC-parity defaults"`
Expected: FAIL — `show_prompt` is `undefined`.

- [ ] **Step 3: Update the type** — in `types.ts`, change `LoopMode` (line ~44):

```ts
export type LoopMode = 'serial' | 'parallel';
```

Extend `LoopNodeData` (after `prompts?: string[];`, ~line 204):

```ts
  /** IC 图片 toggle — this loop takes an image group as input and slices
   *  it per round into the downstream generation's source. */
  image_input?: boolean;
  /** IC 提示词 toggle — this loop contributes a rotating prompt downstream. */
  show_prompt?: boolean;
  /** IC 批次 — images sliced from the upstream group per round, 1..100. */
  image_batch_size?: number;
```

Fix `LOOP_MODE_TONE` (~line 327) — remove the `batch` entry:

```ts
export const LOOP_MODE_TONE: Record<LoopMode, string> = {
  serial: 'border-canvas-line-strong/50',
  parallel: 'border-violet-500',
};
```

- [ ] **Step 4: Update the factory** — in `factories.ts`, replace the loop `data` block (lines 196-202):

```ts
    data: {
      mode: data.mode ?? 'serial',
      label: data.label ?? '',
      rounds: data.rounds ?? 1,
      round_start: data.round_start ?? 1,
      prompts: data.prompts ?? [''],
      show_prompt: data.show_prompt ?? true,
      image_input: data.image_input ?? false,
      image_batch_size: data.image_batch_size ?? 1,
    },
```

- [ ] **Step 5: Run tests + typecheck**

Run: `cd frontend && npx vitest run features/canvas-core/smart/factories.test.ts && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -i "loopmode\|batch" || echo "no batch type refs"`
Expected: factory tests PASS; the grep prints either nothing or the compile sites that still reference `'batch'` (fix any — e.g. a `LOOP_MODE_TONE.batch` read). Then re-run until clean.

- [ ] **Step 6: Commit**

```bash
cd /Volumes/program/project-code/repos/nous/.worktrees/feature-loop-node-ic-parity
git add frontend/features/canvas-core/smart/types.ts frontend/features/canvas-core/smart/factories.ts frontend/features/canvas-core/smart/factories.test.ts
git commit -m "feat(canvas): loop node data model — drop batch mode, add image/prompt/batch fields"
```

---

### Task 2: Slicing helpers — `clampBatchSize`, `sliceLoopImages`

**Files:**
- Modify: `frontend/features/canvas-core/smart/loopVars.ts`
- Test: `frontend/features/canvas-core/smart/loopVars.test.ts` (create if absent)

**Interfaces:**
- Produces: `clampBatchSize(value: number): number` (1..100); `sliceLoopImages<T>(items: readonly T[], index: number, batchSize: number): T[]` — the round-N window `items.slice(index-1, index-1+batchSize)`.

- [ ] **Step 1: Write the failing test** — create/append `loopVars.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { clampBatchSize, sliceLoopImages } from './loopVars';

describe('clampBatchSize', () => {
  it('clamps to 1..100 and floors', () => {
    expect(clampBatchSize(0)).toBe(1);
    expect(clampBatchSize(3.9)).toBe(3);
    expect(clampBatchSize(500)).toBe(100);
    expect(clampBatchSize(NaN)).toBe(1);
  });
});

describe('sliceLoopImages', () => {
  const imgs = ['a', 'b', 'c', 'd'];
  it('windows by round index (IC slice(N-1, N-1+batch))', () => {
    expect(sliceLoopImages(imgs, 1, 1)).toEqual(['a']);
    expect(sliceLoopImages(imgs, 2, 1)).toEqual(['b']);
    expect(sliceLoopImages(imgs, 1, 2)).toEqual(['a', 'b']);
    expect(sliceLoopImages(imgs, 3, 2)).toEqual(['c', 'd']);
  });
  it('over-run yields empty, empty in yields empty', () => {
    expect(sliceLoopImages(imgs, 9, 1)).toEqual([]);
    expect(sliceLoopImages([], 1, 3)).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run features/canvas-core/smart/loopVars.test.ts`
Expected: FAIL — `clampBatchSize`/`sliceLoopImages` not exported.

- [ ] **Step 3: Implement** — append to `loopVars.ts`:

```ts
/** IC imageBatchSize clamp — 1..100, floored. */
export function clampBatchSize(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.max(1, Math.min(100, Math.floor(value)));
}

/**
 * Per-round image window (IC smartLoopInputImages, :12332): for a round
 * whose 《计数》 = index, take items[index-1 .. index-1+batchSize]. A sliding
 * window stepping by 1 per round. Over-run / empty input → empty window.
 */
export function sliceLoopImages<T>(
  items: readonly T[],
  index: number,
  batchSize: number,
): T[] {
  const start = Math.max(0, Math.floor(index) - 1);
  const size = clampBatchSize(batchSize);
  return items.slice(start, start + size);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run features/canvas-core/smart/loopVars.test.ts`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/features/canvas-core/smart/loopVars.ts frontend/features/canvas-core/smart/loopVars.test.ts
git commit -m "feat(canvas): loop image slicing helpers (clampBatchSize, sliceLoopImages)"
```

---

### Task 3: Wire the loop caller to `withGenerationRunner`

**Files:**
- Modify: `frontend/features/canvas-core/smart/loopRun.ts` (imports; `resolveCaller` l42-48; call site l154)
- Test: `frontend/features/canvas-core/smart/loopRun.gen.test.ts` (create)

**Interfaces:**
- Consumes: `withGenerationRunner(base, deps)` (`generationRunner.ts:58`); `dispatchGenerations`/`pollGeneration` (`../services/canvasGenerationService`).
- Produces: `resolveCaller(loopId: string): PromptCaller` — non-override path returns `withGenerationRunner(base, { canvasId, shouldStop })`.

- [ ] **Step 1: Write the failing test** — create `loopRun.gen.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/canvasGenerationService', () => ({
  dispatchGenerations: vi.fn(async () => ['task-1']),
  pollGeneration: vi.fn(async () => ({
    phase: 'completed',
    metadata: { result_url: '/api/v1/generated-media/out.png' },
  })),
  PollStopped: class PollStopped extends Error {},
}));

import { dispatchGenerations } from '../services/canvasGenerationService';
import { __test_resolveCaller } from './loopRun';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

afterEach(() => vi.clearAllMocks());

describe('loop caller drives image generation', () => {
  it('routes a gen prompt to dispatchGenerations and returns its urls', async () => {
    useCanvasCoreStore.setState({ canvasId: 'cv-1' } as never);
    const caller = __test_resolveCaller('loop-1');
    const result = await caller({
      promptId: 'p-1',
      body: 'a cat',
      provider_slug: '',
      agent_id: null,
      gen: { kind: 'image', model: 'm', count: 1 },
      source_url: '/api/v1/generated-media/ref.png',
    });
    expect(dispatchGenerations).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(true);
    expect(result.urls).toEqual(['/api/v1/generated-media/out.png']);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run features/canvas-core/smart/loopRun.gen.test.ts`
Expected: FAIL — `__test_resolveCaller` not exported / caller is text-only so `dispatchGenerations` not called.

- [ ] **Step 3: Implement** — in `loopRun.ts`, add import (after line 24):

```ts
import { withGenerationRunner } from './generationRunner';
```

Replace `resolveCaller` (lines 42-48):

```ts
function resolveCaller(loopId: string): PromptCaller {
  const override = useLoopRunStore.getState().runnerOverride;
  if (override) return override;
  const canvasId = useCanvasCoreStore.getState().canvasId;
  const base = canvasId ? createBackendRunner({ canvasId }) : mockRunner;
  // Loop-driven image prompts carry `gen`+`source_url` in their round
  // contexts (loopRunner). The text-only backend runner dropped them — wrap
  // it so generation dispatches, exactly as the composer/regenerate do.
  return withGenerationRunner(base, {
    canvasId,
    shouldStop: () => useLoopRunStore.getState().isStopRequested(loopId),
  });
}

/** Test seam — exercises the non-override caller path. */
export const __test_resolveCaller = resolveCaller;
```

Update the call site (line 154) inside `startLoopRun`:

```ts
      caller: resolveCaller(loopId),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run features/canvas-core/smart/loopRun.gen.test.ts`
Expected: PASS.

- [ ] **Step 5: Guard against regressions**

Run: `cd frontend && npx vitest run features/canvas-core/smart/nodes/LoopNodeView.run.test.tsx`
Expected: PASS (existing loop-run behavior unchanged — text loops still work because a null `gen` passes straight through `withGenerationRunner`).

- [ ] **Step 6: Commit**

```bash
git add frontend/features/canvas-core/smart/loopRun.ts frontend/features/canvas-core/smart/loopRun.gen.test.ts
git commit -m "feat(canvas): loop run drives image generation (wrap caller in withGenerationRunner)"
```

---

### Task 4: Batch-slice the per-round source image

**Files:**
- Modify: `frontend/features/canvas-core/smart/loopRunner.ts` (import ~30; round-context builder l90-126)
- Test: `frontend/features/canvas-core/smart/loopRunner.test.ts`

**Interfaces:**
- Consumes: `resolveSourceUrls(targetId, nodes, connections): string[]` (`promptInputs.ts:59`); `clampBatchSize`, `sliceLoopImages` (Task 2).
- Produces: when `loop.data.image_input`, each round-N downstream context's `source_url` is overridden with `sliceLoopImages(loopImages, index, batchSize)[0] ?? ctx.source_url`.

- [ ] **Step 1: Write the failing test** — append to `loopRunner.test.ts` (reuse the file's existing node/connection builders; if none, use plain objects as below):

```ts
import { runLoopCascade } from './loopRunner';
import type { RunnerContext } from './runner';

describe('loop image-input batch slicing', () => {
  it('gives each round the next upstream image as source_url', async () => {
    const media = {
      id: 'm1', type: 'media', position: { x: 0, y: 0 },
      data: { items: [
        { url: '/api/v1/generated-media/a.png', kind: 'image' },
        { url: '/api/v1/generated-media/b.png', kind: 'image' },
      ] },
    };
    const loop = {
      id: 'lp', type: 'loop', position: { x: 1, y: 0 },
      data: { mode: 'serial', rounds: 2, round_start: 1, image_input: true, image_batch_size: 1, prompts: [''] },
    };
    const prompt = {
      id: 'pr', type: 'prompt', position: { x: 2, y: 0 },
      data: { body: 'x', gen: { kind: 'image', model: 'm', count: 1 } },
    };
    const nodes = [media, loop, prompt] as never[];
    const connections = [
      { id: 'e1', source: 'm1', target: 'lp', sourceHandle: null, targetHandle: null },
      { id: 'e2', source: 'lp', target: 'pr', sourceHandle: null, targetHandle: null },
    ] as never[];

    const seen: (string | null | undefined)[] = [];
    const caller = async (ctx: RunnerContext) => {
      seen.push(ctx.source_url);
      return { ok: true, text: 'ok', urls: ['/api/v1/generated-media/o.png'], media_kind: 'image' };
    };

    await runLoopCascade({
      loopId: 'lp', nodes, connections, caller,
      handlers: { onStatusChange: () => {} },
    });

    expect(seen).toEqual(['/api/v1/generated-media/a.png', '/api/v1/generated-media/b.png']);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run features/canvas-core/smart/loopRunner.test.ts -t "batch slicing"`
Expected: FAIL — both rounds see the same (null) `source_url` (the loop isn't yet resolving/slicing its own upstream images).

- [ ] **Step 3: Implement** — in `loopRunner.ts`:

Extend the existing promptInputs import (it currently imports `resolveSourceUrl`):

```ts
import { resolveSourceUrl, resolveSourceUrls } from './promptInputs';
```

Extend the loopVars import to add the two helpers:

```ts
import {
  clampBatchSize,
  injectLoopVariables,
  loopRoundIndexes,
  pickRotatingPrompt,
  sliceLoopImages,
} from './loopVars';
```

After the `baseContexts` loop and its empty guard (after line 115), resolve the loop's own upstream images once:

```ts
  // IC image-input (smartLoopInputImages): the loop resolves ITS OWN upstream
  // durable images once, then feeds a per-round slice as the downstream
  // generation's source. A `loop` node is not a durableImagesOf provider, so
  // this must be injected into the downstream contexts (not resolved by them).
  const loopImages = data.image_input
    ? resolveSourceUrls(opts.loopId, opts.nodes, opts.connections)
    : [];
  const batchSize = clampBatchSize(data.image_batch_size ?? 1);
```

Replace `roundContexts` (lines 117-126):

```ts
  const roundContexts = (index: number): RunnerContext[] => {
    const loopPrompt = pickRotatingPrompt(loopPrompts, index);
    // Per-round image window; [0] is our single-source i2i pick (batch>1 =
    // follow-up). Falls back to each prompt's own source when the loop
    // supplies none — regression-safe for text-only loops.
    const sliced = loopImages.length
      ? sliceLoopImages(loopImages, index, batchSize)[0] ?? null
      : null;
    return baseContexts.map((ctx) => ({
      ...ctx,
      source_url: sliced ?? ctx.source_url,
      body: injectLoopVariables(
        loopPrompt ? `${loopPrompt}\n\n${ctx.body}` : ctx.body,
        { index, total },
      ),
    }));
  };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run features/canvas-core/smart/loopRunner.test.ts`
Expected: PASS — including all pre-existing loopRunner tests (text loops unaffected: `loopImages` empty → `sliced` null → `ctx.source_url` unchanged).

- [ ] **Step 5: Lint + full smart-suite gate**

Run: `cd frontend && npm run lint && npx vitest run features/canvas-core/smart`
Expected: lint clean; all smart tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/features/canvas-core/smart/loopRunner.ts frontend/features/canvas-core/smart/loopRunner.test.ts
git commit -m "feat(canvas): loop batch-slices upstream images into per-round source_url"
```

- [ ] **Step 7: Push PR1**

```bash
git push -u origin feature/loop-node-ic-parity
```
(Open PR1 "feat(canvas): loop node drives image generation + batch slicing (logic)". PR2 stacks on the same branch or a follow-up branch per team preference.)

---

# PR2 — UI card

### Task 5: Rewrite `LoopNodeView` to IC card + i18n

**Files:**
- Rewrite: `frontend/features/canvas-core/smart/nodes/LoopNodeView.tsx`
- Modify: `frontend/public/locales/en.json` + `frontend/public/locales/zh.json` (under `"canvas"`, ~line 1730)
- Test: `frontend/features/canvas-core/smart/nodes/smart-node-edit.test.tsx`

**Interfaces:**
- Consumes: `LoopNodeData` (Task 1), `useNodeDataPatch(id)`, `useLoopRunStore`, `startLoopRun`, `useCanvasCoreStore` (for upstream image count), `resolveSourceUrls` (Task 4 export), `clampRounds`/`clampRoundStart`/`clampBatchSize`.
- Produces: node card with `data-testid="smart-loop-node"`, segmented mode buttons (`aria-label` "Serial"/"Parallel"), toggle pills (`aria-label` "Toggle image input"/"Toggle prompt input"), batch/start/rounds number fields, run button.

- [ ] **Step 1: Add i18n keys** — in `en.json`, inside the `"canvas"` object (~line 1730), add:

```json
      "loopModeSerial": "Serial",
      "loopModeParallel": "Parallel",
      "loopToggleImage": "Image",
      "loopTogglePrompt": "Prompt",
      "loopBatch": "Batch",
      "loopStart": "Start",
      "loopRounds": "Rounds",
      "loopRunAll": "Run all",
      "loopCounterToken": "《Count》",
      "loopLabelPlaceholder": "Loop label (optional)",
      "loopPromptPlaceholder": "Prompt for this round — 《Count》 = round index",
      "loopImageWillOutput": "Will output {{n}} image(s)",
      "loopImageEmpty": "Connect an image or image group to the left port"
```

In `zh.json`, same keys under `"canvas"`:

```json
      "loopModeSerial": "循环",
      "loopModeParallel": "并发",
      "loopToggleImage": "图片",
      "loopTogglePrompt": "提示词",
      "loopBatch": "批次",
      "loopStart": "起始计数",
      "loopRounds": "次数",
      "loopRunAll": "一键运行",
      "loopCounterToken": "《计数》",
      "loopLabelPlaceholder": "循环标签（可选）",
      "loopPromptPlaceholder": "本轮提示词 — 《计数》= 当前轮次",
      "loopImageWillOutput": "将输出 {{n}} 张",
      "loopImageEmpty": "把图片或图片组连接到左侧输入口"
```

> The token stays `《计数》` in the injected prompt; only the *button label* is translated. Injection matches on `《计数》`/`[计数]` (`loopVars.injectLoopVariables`) — do not change that. The chip inserts the literal `《计数》` regardless of UI language.

- [ ] **Step 2: Write the failing test** — append to `smart-node-edit.test.tsx`:

```ts
describe('LoopNodeView IC card', () => {
  it('toggles image_input and shows the image panel note', () => {
    const patch = vi.fn();
    // The test harness in this file renders a node via renderNode(...) with a
    // store-backed patch. Follow the existing LoopNodeView test setup above;
    // here we assert on the new controls.
    renderLoopNode({ mode: 'serial', show_prompt: true, image_input: false, image_batch_size: 1, rounds: 1, round_start: 1, prompts: [''] });
    // image panel hidden until toggled
    expect(screen.queryByText(/left port|输入口/i)).toBeNull();
    fireEvent.click(screen.getByLabelText('Toggle image input'));
    expect(screen.getByText(/left port|输入口|Will output/i)).toBeInTheDocument();
  });

  it('switches run mode via the segmented control', () => {
    renderLoopNode({ mode: 'serial', show_prompt: true, image_input: false, image_batch_size: 1, rounds: 2, round_start: 1, prompts: [''] });
    fireEvent.click(screen.getByLabelText('Parallel'));
    // patch called with mode:'parallel' — assert via the store spy used elsewhere in this file.
  });
});
```

> Adapt `renderLoopNode` / patch-spying to the existing helpers already used for `LoopNodeView` in this file (the file mocks `react-i18next` to return the key, so query by key substrings like `Toggle image input`). If the existing helper is `renderNode`, reuse it.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run features/canvas-core/smart/nodes/smart-node-edit.test.tsx -t "IC card"`
Expected: FAIL — no "Toggle image input" control.

- [ ] **Step 4: Rewrite the component** — replace the entire `LoopNodeView.tsx`:

```tsx
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Image as ImageIcon, Plus, Square, TextCursorInput, Workflow, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { LoopMode, LoopNodeData } from '../types';
import { LOOP_MODE_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { clampBatchSize, clampRoundStart, clampRounds } from '../loopVars';
import { startLoopRun } from '../loopRun';
import { useLoopRunStore } from '../loopRunStore';
import { resolveSourceUrls } from '../promptInputs';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { useNodeDataPatch } from './useNodeDataPatch';

export function LoopNodeView({ id, data, selected }: NodeProps) {
  const { t } = useTranslation();
  const d = data as unknown as LoopNodeData;
  const patch = useNodeDataPatch(id);
  const running = useLoopRunStore((s) => Boolean(s.running[id]));
  const stopping = useLoopRunStore((s) => s.running[id]?.stopRequested ?? false);

  // Legacy `mode:'batch'` renders as Serial (batch is no longer a run mode).
  const mode: LoopMode = d.mode === 'parallel' ? 'parallel' : 'serial';
  const tone = LOOP_MODE_TONE[mode];
  const showPrompt = d.show_prompt ?? true;
  const imageInput = d.image_input ?? false;
  const safeRounds = clampRounds(d.rounds ?? 1);
  const safeStart = clampRoundStart(d.round_start ?? 1);
  const safeBatch = clampBatchSize(d.image_batch_size ?? 1);
  const safePrompts = d.prompts && d.prompts.length > 0 ? d.prompts : [''];

  // Upstream durable images feeding this loop (IC preview + "will output N").
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const connections = useCanvasCoreStore((s) => s.connections);
  const upstreamImages = imageInput ? resolveSourceUrls(String(id), nodes, connections) : [];

  const patchPrompt = (index: number, value: string) =>
    patch({ prompts: safePrompts.map((p, i) => (i === index ? value : p)) });
  const insertCounter = (index: number) =>
    patch({ prompts: safePrompts.map((p, i) => (i === index ? `${p}《计数》` : p)) });

  return (
    <div
      data-testid="smart-loop-node"
      className={`mh-node mh-loop-node ${tone} ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.loop }}
    >
      <Handle type="target" position={Position.Left} />

      <div className="mh-loop-card">
        {/* Run mode segmented control */}
        <div className="mh-loop-seg">
          <button
            type="button"
            className={`nodrag mh-loop-seg-btn ${mode === 'serial' ? 'active' : ''}`}
            onClick={() => patch({ mode: 'serial' })}
            aria-label={t('canvas.loopModeSerial')}
          >
            {t('canvas.loopModeSerial')}
          </button>
          <button
            type="button"
            className={`nodrag mh-loop-seg-btn ${mode === 'parallel' ? 'active' : ''}`}
            onClick={() => patch({ mode: 'parallel' })}
            aria-label={t('canvas.loopModeParallel')}
          >
            {t('canvas.loopModeParallel')}
          </button>
        </div>

        {/* Input toggles */}
        <div className="mh-loop-row">
          <button
            type="button"
            className={`nodrag mh-loop-toggle ${imageInput ? 'active' : ''}`}
            onClick={() => patch({ image_input: !imageInput })}
            aria-label="Toggle image input"
          >
            <ImageIcon size={12} />
            <span>{t('canvas.loopToggleImage')}</span>
          </button>
          <button
            type="button"
            className={`nodrag mh-loop-toggle ${showPrompt ? 'active' : ''}`}
            onClick={() => patch({ show_prompt: !showPrompt })}
            aria-label="Toggle prompt input"
          >
            <TextCursorInput size={12} />
            <span>{t('canvas.loopTogglePrompt')}</span>
          </button>
        </div>

        {/* Image panel */}
        {imageInput && (
          <div className="mh-loop-panel">
            <label className="mh-loop-mini">
              <span>{t('canvas.loopBatch')}</span>
              <input
                type="number"
                className="nodrag"
                min={1}
                max={100}
                value={safeBatch}
                onChange={(e) => patch({ image_batch_size: clampBatchSize(Number(e.target.value)) })}
                aria-label={t('canvas.loopBatch')}
              />
            </label>
            <div className="mh-loop-note">
              {upstreamImages.length
                ? t('canvas.loopImageWillOutput', { n: upstreamImages.length })
                : t('canvas.loopImageEmpty')}
            </div>
          </div>
        )}

        {/* Prompt panel */}
        {showPrompt && (
          <div className="mh-loop-panel">
            <div className="mh-loop-prompt-list">
              {safePrompts.map((prompt, i) => (
                <div key={i} className="mh-loop-prompt-item">
                  <span className="mh-loop-prompt-index">{i + 1}</span>
                  <textarea
                    className="nodrag mh-loop-text"
                    placeholder={t('canvas.loopPromptPlaceholder')}
                    value={prompt}
                    rows={1}
                    onChange={(e) => patchPrompt(i, e.target.value)}
                    aria-label={`Loop prompt ${i + 1}`}
                  />
                  <button
                    type="button"
                    className="nodrag mh-loop-icon-btn"
                    onClick={() => patch({ prompts: safePrompts.filter((_p, j) => j !== i) })}
                    disabled={safePrompts.length <= 1}
                    aria-label={`Remove prompt ${i + 1}`}
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
            <div className="mh-loop-prompt-actions">
              <button
                type="button"
                className="nodrag mh-loop-token"
                onClick={() => insertCounter(safePrompts.length - 1)}
                aria-label="Insert count token"
              >
                {t('canvas.loopCounterToken')}
              </button>
              <button
                type="button"
                className="nodrag mh-loop-add"
                onClick={() => patch({ prompts: [...safePrompts, ''] })}
                aria-label="Add prompt"
              >
                <Plus size={12} />
              </button>
            </div>
          </div>
        )}

        {/* Footer: start / rounds / run */}
        <div className="mh-loop-footer">
          <label className="mh-loop-mini">
            <span>{t('canvas.loopStart')}</span>
            <input
              type="number"
              className="nodrag"
              min={1}
              max={9999}
              value={safeStart}
              onChange={(e) => patch({ round_start: clampRoundStart(Number(e.target.value)) })}
              aria-label={t('canvas.loopStart')}
            />
          </label>
          <label className="mh-loop-mini">
            <span>{t('canvas.loopRounds')}</span>
            <input
              type="number"
              className="nodrag"
              min={1}
              max={100}
              value={safeRounds}
              onChange={(e) => patch({ rounds: clampRounds(Number(e.target.value)) })}
              aria-label={t('canvas.loopRounds')}
            />
          </label>
          <button
            type="button"
            className={`nodrag mh-loop-run ${running ? 'is-stop' : ''}`}
            onClick={() => {
              if (running) {
                useLoopRunStore.getState().requestStop(id);
                return;
              }
              startLoopRun(id).catch((err) => console.error('[LoopNodeView] loop run failed:', err));
            }}
            disabled={stopping}
            aria-label={running ? 'Stop loop' : 'Run loop'}
            title={running ? (stopping ? 'Stopping…' : 'Stop after this round') : t('canvas.loopRunAll')}
          >
            {running ? <Square size={11} /> : <Workflow size={11} />}
            <span>{running ? '' : t('canvas.loopRunAll')}</span>
          </button>
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run features/canvas-core/smart/nodes/smart-node-edit.test.tsx features/canvas-core/smart/nodes/LoopNodeView.run.test.tsx`
Expected: PASS. If the existing `LoopNodeView.run.test.tsx` queried the old dropdown/label DOM, update its selectors to the new controls (segmented buttons via `getByLabelText('Serial')`, run via `getByLabelText('Run loop')`).

- [ ] **Step 6: Lint**

Run: `cd frontend && npm run lint`
Expected: clean (react-hooks: `useCanvasCoreStore` selectors are unconditional at top level — no conditional hooks).

- [ ] **Step 7: Commit**

```bash
git add frontend/features/canvas-core/smart/nodes/LoopNodeView.tsx frontend/features/canvas-core/smart/nodes/smart-node-edit.test.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(canvas): loop node IC card — segmented mode, image/prompt toggles, counter chip"
```

---

### Task 6: `.mh-loop-*` styling (frosted card, segments, toggles, panels, footer)

**Files:**
- Modify: `frontend/index.css` (append after the `.mh-node-title` block, ~line 852)

**Interfaces:**
- Consumes: existing tokens `--canvas-panel`, `--canvas-line`, `--canvas-line-strong`, `--canvas-strong` (defined `index.css:717-743`, both themes).
- Produces: `.mh-loop-node`, `.mh-loop-card`, `.mh-loop-seg`, `.mh-loop-seg-btn`, `.mh-loop-row`, `.mh-loop-toggle`, `.mh-loop-panel`, `.mh-loop-mini`, `.mh-loop-note`, `.mh-loop-prompt-*`, `.mh-loop-token`, `.mh-loop-add`, `.mh-loop-icon-btn`, `.mh-loop-footer`, `.mh-loop-run`.

- [ ] **Step 1: Add the CSS** — append to `index.css` (mirrors IC's `.loop-smart-*`, `smart-canvas.css:211-237`, adapted to our tokens + theme-aware):

```css
/* ── Loop node IC card (parity with Infinite .loop-smart-*) ─────────── */
.mh-loop-node { padding: 0; }
.mh-loop-card { display: flex; flex-direction: column; gap: 8px; padding: 12px; }
.mh-loop-row { display: flex; align-items: center; gap: 7px; }

.mh-loop-seg {
  display: grid; grid-template-columns: 1fr 1fr; gap: 2px; padding: 2px;
  border-radius: 10px; border: 1px solid var(--canvas-line);
  background: color-mix(in srgb, var(--canvas-panel) 60%, transparent);
}
.mh-loop-seg-btn {
  height: 25px; border-radius: 9px; background: transparent;
  color: var(--canvas-line-strong); font-size: 11px; font-weight: 700;
  transition: background .14s ease, color .14s ease;
}
.mh-loop-seg-btn:hover { color: var(--canvas-strong); }
.mh-loop-seg-btn.active { background: var(--canvas-strong); color: var(--canvas-panel); }

.mh-loop-toggle {
  flex: 1 1 0; height: 30px; border-radius: 11px; padding: 0 9px;
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  border: 1px solid var(--canvas-line); background: transparent;
  color: var(--canvas-line-strong); font-size: 11px; font-weight: 700;
  transition: border-color .14s ease, color .14s ease, background .14s ease;
}
.mh-loop-toggle:hover { border-color: var(--canvas-line-strong); color: var(--canvas-strong); }
.mh-loop-toggle.active {
  color: var(--canvas-strong); border-color: var(--canvas-strong);
  background: color-mix(in srgb, var(--canvas-strong) 8%, transparent);
}

.mh-loop-panel {
  display: flex; flex-direction: column; gap: 6px; padding: 8px;
  border-radius: 12px; border: 1px solid var(--canvas-line);
  background: color-mix(in srgb, var(--canvas-panel) 40%, transparent);
}
.mh-loop-mini {
  display: inline-flex; align-items: center; gap: 5px;
  color: var(--canvas-line-strong); font-size: 10.5px; font-weight: 700;
}
.mh-loop-mini input {
  width: 46px; height: 24px; text-align: center; border-radius: 8px;
  border: 1px solid var(--canvas-line); background: transparent;
  color: var(--canvas-strong); font-size: 11px; font-weight: 800; outline: none;
}
.mh-loop-note { color: var(--canvas-line-strong); font-size: 10px; line-height: 1.35; }

.mh-loop-prompt-list { display: flex; flex-direction: column; gap: 6px; }
.mh-loop-prompt-item { display: flex; align-items: flex-start; gap: 5px; }
.mh-loop-prompt-index { padding-top: 6px; font-size: 10px; color: var(--canvas-line-strong); }
.mh-loop-text {
  flex: 1; min-height: 34px; resize: vertical; border-radius: 10px;
  border: 1px solid var(--canvas-line); background: transparent; padding: 6px 8px;
  color: var(--canvas-strong); font-size: 12px; outline: none;
}
.mh-loop-text::placeholder { color: var(--canvas-line-strong); }
.mh-loop-prompt-actions { display: flex; align-items: center; gap: 6px; }
.mh-loop-token {
  height: 24px; padding: 0 8px; border-radius: 8px;
  border: 1px solid var(--canvas-line); background: color-mix(in srgb, var(--canvas-strong) 6%, transparent);
  color: var(--canvas-strong); font-size: 11px; font-weight: 700;
}
.mh-loop-add, .mh-loop-icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  height: 24px; width: 24px; border-radius: 8px; color: var(--canvas-line-strong);
}
.mh-loop-add:hover, .mh-loop-icon-btn:hover:not(:disabled) { color: var(--canvas-strong); }
.mh-loop-icon-btn:disabled { opacity: .4; cursor: not-allowed; }

.mh-loop-footer { display: flex; align-items: center; gap: 7px; }
.mh-loop-run {
  margin-left: auto; height: 30px; padding: 0 12px; border-radius: 11px;
  display: inline-flex; align-items: center; gap: 5px;
  background: var(--canvas-strong); color: var(--canvas-panel);
  font-size: 11px; font-weight: 800;
}
.mh-loop-run:disabled { opacity: .5; cursor: not-allowed; }
.mh-loop-run.is-stop { background: #e11d48; color: #fff; }
```

- [ ] **Step 2: Visual check via Vercel preview**

Per repo convention (`feedback_verify_via_vercel_preview_not_local`), push and open the PR2 preview. Manually verify against IC's screenshot: segmented Serial/Parallel, Image/Prompt pills, batch panel, prompt list with 《Count》 chip, footer Start/Rounds/Run — in both light and dark theme.

Run: `cd frontend && npm run build`
Expected: build succeeds (no CSS/TS errors).

- [ ] **Step 3: Commit + push**

```bash
git add frontend/index.css
git commit -m "feat(canvas): loop node IC card styling (.mh-loop-*)"
git push
```

---

## Self-Review (author's notes — all resolved)

- **Spec §3.1 (data model):** Task 1. **§3.2 (helpers):** Task 2. **§3.3 (caller wire):** Task 3. **§3.4 (slicing + injection):** Task 4. **§3.5 (UI card):** Tasks 5-6. **Batch-mode removal + legacy coercion:** Task 1 (type) + Task 5 (`mode === 'parallel' ? parallel : serial` render). **Migration defaults:** Task 1 factory + Task 5 `?? true`/`?? false`/`?? 1` reads.
- **Type consistency:** `clampBatchSize`/`sliceLoopImages` (Task 2) consumed identically in Tasks 4-5; `resolveSourceUrls` (existing) consumed in Tasks 4-5; `LoopNodeData` fields `image_input`/`show_prompt`/`image_batch_size` named identically everywhere.
- **Out of scope (spec §6):** multi-ref i2i (batch>1 uses `window[0]`), other node types, group-member loop rendering.
- **Known adaptation point:** Task 5 Step 2/5 tests must be fitted to the existing `smart-node-edit.test.tsx` / `LoopNodeView.run.test.tsx` render helpers and i18n passthrough already in those files — selectors switch from the old dropdown to the new segmented/toggle controls.
