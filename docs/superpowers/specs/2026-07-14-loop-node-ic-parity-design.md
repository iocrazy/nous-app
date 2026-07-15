# Loop Node → Infinite-Canvas Parity — Design

**Date:** 2026-07-14
**Status:** Approved (design), pending spec review
**Scope:** The smart-canvas **Loop node** only — first node in the node-by-node IC alignment pass.
**Rollout:** Two PRs, no feature flag.

---

## 1. Problem

Our Loop node (`frontend/features/canvas-core/smart/nodes/LoopNodeView.tsx`) diverges from the
Infinite-Canvas (IC) reference (`static/js/smart-canvas.js::smartLoopBodyHtml`, l7051) in both
**visual design** and **logic**:

### Logic defects
1. **`'batch'` run-mode is dead and conceptually wrong.** `LoopMode` (`types.ts:44`) is
   `'serial' | 'parallel' | 'batch'`, but `loopRunner.ts:135` only branches `parallel` vs not —
   selecting **Batch** silently runs serially. IC has no "batch" run-mode at all; 批次 (batch size)
   is an **image-input** dimension, orthogonal to serial/parallel. Mixing it into `mode` is the
   root logic error.
2. **No image input.** Our loop cascade runs **text prompt** nodes only
   (`POST /api/v1/canvases/runs/prompts`), producing text output slots. IC's loop can take an
   image group as input, slice it per round, and drive **image generation** — the core IC use
   case (screenshot: "把图片或图片组连接到左侧输入口 · 现在生成第 [计数] 张卖点图片").

### Visual defects
Flat white card with a dropdown + a lone header run button. IC uses a structured frosted card:
segmented run-mode, image/prompt toggle pills, input panels, and a footer with counters + run.

### What already works (verified, do NOT rebuild)
- Image-generation pipeline: `withGenerationRunner` → dispatch/poll → progressive output slots
  (`generationRunner.ts`, `genSlots.ts`).
- The loop cascade **already builds per-round `RunnerContext`s carrying `gen` + `source_url`**
  (`loopRunner.ts:101-111`).
- The output side **already consumes `result.urls`/`media_kind`** into per-round media slots
  (`loopRun.ts::upsertRoundSlot`).
- `media → loop` connection is **already valid** (`types.ts:298`).
- `serial` / `parallel` run modes work correctly (`loopRunner.ts:135`).

**Conclusion:** full IC parity is *extend, not rebuild*.

---

## 2. IC reference semantics (the target)

From `smartLoopBodyHtml` (l7051) and helpers:

- **Run mode** (`loop-smart-seg`): 循环/并发 = `serial` | `parallel` only.
- **Input toggles** (`loop-smart-toggle`): 图片 (`imageInput`) and 提示词 (`showPrompt`) —
  independent booleans deciding which input panels show and what the loop feeds downstream.
- **Image panel** (when `imageInput`): upstream thumbnails + 批次 (`imageBatchSize`, 1..100) +
  "将输出 N 张" note.
- **Prompt panel** (when `showPrompt`): upstream-prompt preview + rotating prompt list
  (contenteditable) + **《计数》 chip** insert button + add-row.
- **Footer** (`loop-smart-footer`): 起始计数 (`loopStart`) + 次数 (`loopCount`/`count`) + 一键运行.
- **Image slicing** (`smartLoopInputImages`, l12322): for a round whose 《计数》 = **N**, the source
  images are `upstreamImages.slice(N-1, N-1 + imageBatchSize)` — a sliding window of size
  `imageBatchSize` stepping by 1 per round. `N` ranges `loopStart, loopStart+1, …`.

---

## 3. Design

### 3.1 Data model (`types.ts`, `factories.ts`)

Add three **optional** fields to `LoopNodeData` (optional ⇒ pre-existing canvases keep loading):

```ts
export interface LoopNodeData {
  mode: LoopMode;              // now 'serial' | 'parallel' (see below)
  label: string;
  rounds?: number;             // 1..100 (IC count)
  round_start?: number;        // ≥1 (IC loopStart)
  prompts?: string[];          // rotating; round N uses (N-1) % length
  image_input?: boolean;       // NEW — IC imageInput toggle
  show_prompt?: boolean;       // NEW — IC showPrompt toggle
  image_batch_size?: number;   // NEW — IC imageBatchSize, 1..100
}
```

- **Remove `'batch'`** from `LoopMode` → `'serial' | 'parallel'`; drop `LOOP_MODE_TONE.batch`.
- **Legacy coercion:** normalize `mode === 'batch'` → `'serial'` at read time (view + factory +
  any `runLoopCascade`/factory default). No DB migration needed — normalization on load is enough
  since loops persist inside `nodes_json`.
- **Migration defaults for existing loops** (applied lazily on first render / via safe defaults):
  `show_prompt` defaults to `true` when the field is absent **and** the node has prompts (keeps
  current always-shows-prompt behavior); `image_input` defaults `false`; `image_batch_size`
  defaults `1`.
- **Factory `createLoopNode`:** fresh node defaults `mode:'serial'`, `show_prompt:true`,
  `image_input:false`, `image_batch_size:1`, `rounds:1`, `round_start:1`, `prompts:['']`.

### 3.2 Pure helpers (`loopVars.ts` or new `loopImages.ts`)

Add pure, unit-tested helpers (mirroring IC, no store access):

- `clampBatchSize(n): number` → `max(1, min(100, floor(n||1)))`.
- `sliceLoopImages(images: readonly T[], index: number, batchSize: number): T[]` →
  `images.slice(index-1, index-1 + batchSize)` (index = round 《计数》 value). Empty in ⇒ empty out.

### 3.3 Run wiring — the one missing wire (`loopRun.ts::resolveCaller`, l42-48)

Wrap the caller in `withGenerationRunner` + progressive-slot callbacks, exactly as
`regenerate.ts:49` / `CanvasComposer.tsx:132-161` already do:

```ts
function resolveCaller(): PromptCaller {
  const override = useLoopRunStore.getState().runnerOverride;
  if (override) return override;
  const canvasId = useCanvasCoreStore.getState().canvasId;
  const base = canvasId ? createBackendRunner({ canvasId }) : mockRunner;
  return withGenerationRunner(base, { /* same slot callbacks the composer uses */ });
}
```

This makes a loop run drive **image generation** — the `gen`/`source_url` already flow through the
round contexts; they were being dropped by the text-only backend runner.

### 3.4 Batch slicing in the round-context builder (`loopRunner.ts` — net-new)

Topology: the loop sits **between** the source and the generation — `media → loop → prompt`
(both edges valid: `types.ts:298,260`). A `loop` node is **not** a `durableImagesOf` provider,
so the downstream prompt cannot resolve the image itself; the loop must **inject** its per-round
sliced source into the downstream prompt round-contexts.

When the loop's `image_input` is on:

1. Resolve the loop's **own** upstream **durable** images once — reuse `durableImagesOf` over the
   loop node's direct inputs (`inputNodesFor(loop)`), collecting media/group/output image URLs
   (`/api/v1/generated-media/` durability gate, `promptInputs.ts:18`).
2. For each round index N, compute `window = sliceLoopImages(images, N, batchSize)`.
3. When building each **downstream prompt's** context for round N, override its `source_url` with
   `window[0]?.url` (our dispatch takes a **single** `source_url`, `generationRunner.ts:91`). This
   override applies only when the loop supplies image input; otherwise the prompt's own
   `resolveSourceUrl` is used unchanged (regression-safe for text-only loops).

**Scoping boundary:** `image_batch_size > 1` as *multiple simultaneous i2i refs* requires backend
multi-source support that does not exist today — so batch>1 currently feeds only the first image of
the window as `source_url`. The primary IC use case (batch = 1, stepping one image per round) is
fully supported. Multi-ref i2i is an explicit **follow-up**, not part of this work.

### 3.5 UI card rewrite (`LoopNodeView.tsx` + canvas stylesheet)

Rewrite the node body to IC structure, using our React/Tailwind idiom and existing canvas CSS
tokens (`--canvas-panel`, `--canvas-line`, `--canvas-strong`, `.mh-node` family — matching the
recent #1352/#1353 IC visual-alignment PRs):

- **Top:** segmented 循环/并发 control (replaces the `UiSelect` dropdown).
- **Row:** 图片 / 提示词 toggle pills (drive `image_input` / `show_prompt`).
- **Image panel** (when `image_input`): upstream thumbnails (reuse existing thumb rendering) +
  批次 number control + "将输出 N 张 / 把图片连接到左侧输入口" note.
- **Prompt panel** (when `show_prompt`): rotating prompt list + **《计数》 chip** insert button +
  add-row. (Chip = insert the `《计数》` token at caret; keep textarea/contenteditable consistent
  with our other prompt inputs.)
- **Footer:** 起始计数 (`round_start`) + 次数 (`rounds`) + 一键运行 (Run ↔ Stop, existing
  `startLoopRun`/`requestStop`).
- New CSS classes mirroring IC's `.loop-smart-*` (frosted card, segments, toggle pills, panels,
  footer), theme-aware (light/dark).

> **UI copy:** all user-facing strings go through i18n (`en.json`/`zh.json`) with English keys —
> per project UI-language rule. Chinese labels above are the IC reference, not literals to hardcode.

---

## 4. Rollout — two PRs, no flag

**PR1 — logic (`feature/loop-node-ic-parity`, off master):**
- Data-model change (add fields, remove `'batch'`, legacy coercion) + factory defaults.
- `resolveCaller` → `withGenerationRunner`.
- Batch-slicing helpers + round-context wiring.
- Tests: `loopVars`/`loopImages` slicing units; `loopRunner.test.ts` per-round image contexts;
  `LoopNodeView.run.test.tsx` image-input caller drives generation; legacy `mode:'batch'` coercion.
- Ships as internal wiring; the current UI keeps working (mode dropdown loses `Batch`).

**PR2 — UI card:**
- Full `LoopNodeView.tsx` rewrite to IC card + CSS + i18n keys.
- Tests: `smart-node-edit.test.tsx` toggles/fields; render snapshot of the new card.

Direct-to-master, review on Vercel preview / prod per the team's ship-and-review flow.

---

## 5. Test strategy

- **Unit:** slicing window correctness (start base, batch size, over-run clamps to empty),
  batch-size clamp, `'batch'`→`'serial'` coercion.
- **Integration (component):** loop with an `image_input` upstream media node → run → each round's
  dispatched context carries the correct sliced `source_url`; output slots receive URLs.
- **Regression:** existing text-only loop (no image input, `show_prompt:true`) behaves exactly as
  today; `parallel` mode still fans out.
- Follow the repo's Vitest + Testing Library patterns already in the `smart/` test files.

---

## 6. Out of scope (explicit)

- Multi-ref i2i (image_batch_size > 1 feeding multiple simultaneous source images) — backend
  follow-up.
- Any other node type — this pass is Loop only; the node-by-node IC comparison continues
  separately.
- Loop-inside-group member rendering beyond what already exists.
