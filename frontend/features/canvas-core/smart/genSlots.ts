// features/canvas-core/smart/genSlots.ts
//
// Per-prompt generation output slot (Infinite-Canvas parity G4-F2). ALL of
// a run's result URLs land in ONE output node right of the prompt (Infinite:
// count N = one node, N images), tagged data.gen_slot {node_id} and reused
// on re-runs — the previous set is archived into the slot's history node
// (replaceOutputImagesWithHistory). Insertion is history-free: operational
// output must not evict the user's undo stack.

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasConnection, CanvasNode } from '../types';
import { createOutputNode } from './factories';
import { replaceOutputImagesWithHistory } from './outputHistory';
import type { GeneratedImageRef, OutputKind } from './types';

const SLOT_OFFSET_X = 320;

interface GenSlotTag {
  node_id: string;
  index: number;
}

const asObj = (n: unknown) => n as Record<string, unknown>;

function genSlotTagOf(node: unknown): GenSlotTag | undefined {
  const data = asObj(node).data as { gen_slot?: GenSlotTag } | undefined;
  return data?.gen_slot;
}

/** Create-or-update the output slot for one prompt's generation results. */
export function upsertGenerationSlots(
  promptId: string,
  urls: string[],
  mediaKind: OutputKind,
): void {
  const store = useCanvasCoreStore.getState();
  const prompt = store.nodes.find((n) => asObj(n).id === promptId);
  if (!prompt) return;

  const existing = store.nodes.find((n) => genSlotTagOf(n)?.node_id === promptId);
  if (existing) {
    replaceOutputImagesWithHistory(String(asObj(existing).id), urls, mediaKind);
    return;
  }

  const promptPos =
    (asObj(prompt).position as { x: number; y: number }) ?? { x: 0, y: 0 };
  const base = createOutputNode(
    { kind: mediaKind, preview_url: urls[0] ?? null, preview_text: '' },
    { position: { x: promptPos.x + SLOT_OFFSET_X, y: promptPos.y } },
  );
  const slot = {
    ...base,
    data: {
      ...(base.data as unknown as Record<string, unknown>),
      images: urls.map((url): GeneratedImageRef => ({ url, kind: mediaKind })),
      gen_slot: { node_id: promptId, index: 0 } satisfies GenSlotTag,
    },
  } as CanvasNode;
  const edge: CanvasConnection = {
    id: `edge-${crypto.randomUUID()}`,
    source: promptId,
    target: String(asObj(slot).id),
    sourceHandle: null,
    targetHandle: null,
  };
  useCanvasCoreStore.getState().appendElementsNoHistory([slot], [edge]);
}

// ── Progressive placeholders (P0-3) ─────────────────────────────────────────
// Infinite's pendingBoxSize + loading-cell pattern: the slot appears the
// moment a run is DISPATCHED (shimmer cells sized by count), each finished
// item replaces a cell as it lands (first-done-first-shown), failures keep
// their count on the node. All writes history-free, matching the slot's
// operational-state contract.

/** FALLBACK only — the ratio as the prompt node has it written down.
 *
 *  This is not what the run uses on the default image path: `'auto'` (which
 *  is what the picker shows) and an unset value both mean "follow the source
 *  image", and the runner resolves them by measuring it at dispatch. Callers
 *  that know the dispatched value pass it in and this is not consulted; it
 *  covers the ones that have none (recover marks, legacy entry points), and
 *  the video side, where the aspect is sent verbatim anyway. */
function requestedRatioOf(prompt: unknown, mediaKind: OutputKind): string | null {
  const gen = (asObj(prompt).data as { gen?: { ratio?: string; aspect?: string } })
    ?.gen;
  if (!gen) return null;
  // Image prompts carry `ratio`, video prompts `aspect` (PromptGenSettings);
  // each falls back to the other so a mis-tagged node still reserves a box.
  const raw = mediaKind === 'video' ? (gen.aspect ?? gen.ratio) : (gen.ratio ?? gen.aspect);
  return raw ?? null;
}

/** Find the slot node id for a prompt, if one exists. */
function slotIdFor(promptId: string): string | null {
  const store = useCanvasCoreStore.getState();
  const existing = store.nodes.find((n) => genSlotTagOf(n)?.node_id === promptId);
  return existing ? String(asObj(existing).id) : null;
}

/** Dispatch time: create the slot (or archive the previous batch) and show
 *  `count` shimmer placeholders. */
export function beginGenerationSlot(
  promptId: string,
  count: number,
  mediaKind: OutputKind,
  /** The aspect the dispatch actually sent (already auto-resolved). `null`
   *  or omitted falls back to the prompt's own value. */
  dispatchedRatio: string | null = null,
): void {
  const store = useCanvasCoreStore.getState();
  const prompt = store.nodes.find((n) => asObj(n).id === promptId);
  if (!prompt) return;

  // Recomputed every run: the user can change the ratio between runs, and
  // the slot is reused, so a stamp left at the first run's value would
  // reserve the wrong box for the rest of the node's life.
  const genRatio = dispatchedRatio ?? requestedRatioOf(prompt, mediaKind);

  const existingId = slotIdFor(promptId);
  if (existingId) {
    // Archive the previous batch ONCE per run; the fresh batch then fills
    // progressively via appendGenerationResults.
    replaceOutputImagesWithHistory(existingId, [], mediaKind);
    store.patchNode(existingId, {
      data: { gen_pending: count, gen_failed: 0, gen_ratio: genRatio },
    });
    return;
  }

  const promptPos =
    (asObj(prompt).position as { x: number; y: number }) ?? { x: 0, y: 0 };
  const base = createOutputNode(
    { kind: mediaKind, preview_url: null, preview_text: '' },
    { position: { x: promptPos.x + SLOT_OFFSET_X, y: promptPos.y } },
  );
  const slot = {
    ...base,
    data: {
      ...(base.data as unknown as Record<string, unknown>),
      images: [],
      gen_pending: count,
      gen_failed: 0,
      gen_ratio: genRatio,
      gen_slot: { node_id: promptId, index: 0 } satisfies GenSlotTag,
    },
  } as CanvasNode;
  const edge: CanvasConnection = {
    id: `edge-${crypto.randomUUID()}`,
    source: promptId,
    target: String(asObj(slot).id),
    sourceHandle: null,
    targetHandle: null,
  };
  useCanvasCoreStore.getState().appendElementsNoHistory([slot], [edge]);
}

/** One item finished: replace a shimmer cell with the real image. */
export function appendGenerationResults(
  promptId: string,
  urls: string[],
  mediaKind: OutputKind,
): void {
  if (urls.length === 0) return;
  const slotId = slotIdFor(promptId);
  if (!slotId) {
    // No begin ran (legacy path) — fall back to the batch upsert.
    upsertGenerationSlots(promptId, urls, mediaKind);
    return;
  }
  const store = useCanvasCoreStore.getState();
  const node = store.nodes.find((n) => asObj(n).id === slotId);
  const data = (asObj(node).data ?? {}) as {
    images?: GeneratedImageRef[];
    gen_pending?: number;
  };
  const images = [
    ...(data.images ?? []),
    ...urls.map((url): GeneratedImageRef => ({ url, kind: mediaKind })),
  ];
  store.patchNode(slotId, {
    data: {
      kind: mediaKind,
      images,
      preview_url: images[0]?.url ?? null,
      gen_pending: Math.max(0, (data.gen_pending ?? 0) - urls.length),
    },
  });
}

/**
 * A run reached a terminal status: no cell may still be pending.
 *
 * The runner calls `onItemSettled` inside each task's own `.then`, and only
 * emits the terminal status after `Promise.all` over those tasks resolves —
 * so by the time this runs, every dispatched task HAS settled. A cell still
 * pending here is one nothing will ever settle, and it pulses forever.
 *
 * `healStaleGenSlots` already clamps this class, but only at LOAD. The
 * invariant does not break at load; it breaks here, in front of the user —
 * which is why the shimmer stopped only when someone happened to reload
 * (reported 2026-09-02, and again 2026-09-08 with the same screenshot: one
 * delivered image beside one pulsing cell). Load-time stays as the backstop
 * for a tab that was closed mid-run.
 *
 * It WARNS rather than correcting silently. Reserving a cell nothing settles
 * is an upstream bug that is still unexplained, and a silent correction is
 * exactly how it stayed that way — the log is the only place it becomes
 * visible now that the symptom is gone.
 */
export function settleRunTerminal(promptId: string): void {
  const slotId = slotIdFor(promptId);
  if (!slotId) return; // text runs have no slot — nothing to settle.
  const store = useCanvasCoreStore.getState();
  const node = store.nodes.find((n) => asObj(n).id === slotId);
  const data = (asObj(node).data ?? {}) as { gen_pending?: number };
  const pending = data.gen_pending ?? 0;
  // The clean case is every case but one, and it runs on every terminal
  // status: writing here would churn node identity and dirty the canvas for
  // a pure runtime no-op.
  if (pending <= 0) return;
  console.warn(
    `[genSlots] run ${promptId} went terminal with ${pending} unsettled cell(s) — ` +
      'a task was reserved that nothing ever settled. Clearing the shimmer; ' +
      'the reservation leak upstream is still unexplained.',
  );
  store.patchNode(slotId, { data: { gen_pending: 0 } });
}

// ── Recover marks (P1-13) ───────────────────────────────────────────────────
// Infinite's imageTaskRecover state: a broken POLL is not a failed TASK —
// the backend keeps running it. The slot swaps that item's shimmer cell for
// a "task not lost" overlay carrying the task id, re-queryable on demand
// (genResume.requeryRecoverTask). Persisted in nodes_json → survives reload.

/** Swap one pending cell for a recover entry (poll broke, task not lost).
 *  Creates the slot when missing — a reload can land before the dispatch's
 *  own slot write was autosaved, and the recover mark must not vanish. */
export function markGenerationRecover(
  promptId: string,
  taskId: string,
  kind: OutputKind = 'image',
): void {
  let slotId = slotIdFor(promptId);
  if (!slotId) {
    beginGenerationSlot(promptId, 0, kind);
    slotId = slotIdFor(promptId);
    if (!slotId) return;
  }
  const store = useCanvasCoreStore.getState();
  const node = store.nodes.find((n) => asObj(n).id === slotId);
  const data = (asObj(node).data ?? {}) as {
    gen_pending?: number;
    gen_recover?: string[];
  };
  const recover = data.gen_recover ?? [];
  if (recover.includes(taskId)) return;
  store.patchNode(slotId, {
    data: {
      gen_pending: Math.max(0, (data.gen_pending ?? 0) - 1),
      gen_recover: [...recover, taskId],
    },
  });
}

/** Settle a recover entry after a re-query: url lands as an image, null
 *  burns into the failed count. Never touches gen_pending — sibling tasks
 *  still in flight own those cells. */
export function resolveGenerationRecover(
  promptId: string,
  taskId: string,
  outcome: { url: string | null; kind: OutputKind },
): void {
  const slotId = slotIdFor(promptId);
  if (!slotId) return;
  const store = useCanvasCoreStore.getState();
  const node = store.nodes.find((n) => asObj(n).id === slotId);
  const data = (asObj(node).data ?? {}) as {
    images?: GeneratedImageRef[];
    gen_recover?: string[];
    gen_failed?: number;
  };
  const recover = data.gen_recover ?? [];
  if (!recover.includes(taskId)) return;
  const rest = recover.filter((id) => id !== taskId);
  if (outcome.url) {
    const images = [
      ...(data.images ?? []),
      { url: outcome.url, kind: outcome.kind } satisfies GeneratedImageRef,
    ];
    store.patchNode(slotId, {
      data: {
        kind: outcome.kind,
        images,
        preview_url: images[0]?.url ?? null,
        gen_recover: rest,
      },
    });
    return;
  }
  store.patchNode(slotId, {
    data: { gen_recover: rest, gen_failed: (data.gen_failed ?? 0) + 1 },
  });
}

/** Item(s) failed or the run was stopped: burn pending cells down. */
export function settleGenerationSlot(
  promptId: string,
  opts: { failed?: number; clearPending?: boolean },
): void {
  const slotId = slotIdFor(promptId);
  if (!slotId) return;
  const store = useCanvasCoreStore.getState();
  const node = store.nodes.find((n) => asObj(n).id === slotId);
  const data = (asObj(node).data ?? {}) as {
    gen_pending?: number;
    gen_failed?: number;
  };
  const failed = opts.failed ?? 0;
  store.patchNode(slotId, {
    data: {
      gen_pending: opts.clearPending
        ? 0
        : Math.max(0, (data.gen_pending ?? 0) - failed),
      gen_failed: (data.gen_failed ?? 0) + failed,
    },
  });
}
