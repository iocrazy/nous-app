// features/canvas-core/smart/loopRun.ts
//
// Store-level wiring for the from-loop run (Phase 1 G3b): binds
// runLoopCascade to the live canvas store — statuses patch through the
// same channel as the composer's Cascade Run, and every successful round
// lands in a per-round output slot to the right of the tail prompt
// (Infinite's createLoopOutputSlot: stacked vertically, reused by
// (loop_id, round_index) on re-runs instead of duplicating).
//
// The run snapshots the graph AND the canvasId once at start (matching
// Infinite's runState): mid-run edits don't reroute remaining rounds, and
// every store write is guarded against the singleton canvasCoreStore
// having switched to ANOTHER canvas mid-run — without the guard an
// in-flight run would autosave slots and dangling edges into the wrong
// document.

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';
import { createOutputNode } from './factories';
import { runLoopCascade, type LoopRunSummary } from './loopRunner';
import { useLoopRunStore } from './loopRunStore';
import { createBackendRunner } from './runner.backend';
import { mockRunner, type PromptCaller, type RunnerResult } from './runner';
import { downstreamPrompts, topoSortPrompts } from './topology';

const SLOT_OFFSET_X = 320;
const SLOT_SPACING_Y = 150;

interface LoopSlotTag {
  loop_id: string;
  round_index: number;
}

const asObj = (n: unknown) => n as Record<string, unknown>;

function slotTagOf(node: unknown): LoopSlotTag | undefined {
  const data = asObj(node).data as { loop_slot?: LoopSlotTag } | undefined;
  return data?.loop_slot;
}

function resolveCaller(): PromptCaller {
  const override = useLoopRunStore.getState().runnerOverride;
  if (override) return override;
  const canvasId = useCanvasCoreStore.getState().canvasId;
  if (canvasId) return createBackendRunner({ canvasId });
  return mockRunner;
}

/** Create-or-update the output slot for one successful round. */
function upsertRoundSlot(args: {
  loopId: string;
  tailId: string;
  roundIndex: number;
  roundStart: number;
  results: RunnerResult[];
}): void {
  const store = useCanvasCoreStore.getState();
  const text = args.results[args.results.length - 1]?.text ?? '';
  const preview = `Run ${args.roundIndex}: ${text}`;

  const existing = store.nodes.find((n) => {
    const tag = slotTagOf(n);
    return tag?.loop_id === args.loopId && tag?.round_index === args.roundIndex;
  });
  if (existing) {
    store.patchNode(String(asObj(existing).id), { data: { preview_text: preview } });
    return;
  }

  // The anchor may have been deleted mid-run — skip rather than dropping
  // a slot at the origin with a dangling edge.
  const tail = store.nodes.find((n) => asObj(n).id === args.tailId);
  if (!tail) return;
  const tailPos = (asObj(tail).position as { x: number; y: number }) ?? { x: 0, y: 0 };

  const base = createOutputNode(
    { kind: 'text', preview_text: preview },
    {
      position: {
        x: tailPos.x + SLOT_OFFSET_X,
        y: tailPos.y + (args.roundIndex - args.roundStart) * SLOT_SPACING_Y,
      },
    },
  );
  const slot = {
    ...base,
    data: {
      ...(base.data as unknown as Record<string, unknown>),
      loop_slot: { loop_id: args.loopId, round_index: args.roundIndex } satisfies LoopSlotTag,
    },
  } as CanvasNode;

  // One atomic, history-free append: slots are operational output (like
  // run_status), not user edits — they must not evict the undo stack.
  store.appendElementsNoHistory(
    [slot],
    [
      {
        id: `edge-${crypto.randomUUID()}`,
        source: args.tailId,
        target: String(asObj(slot).id),
        sourceHandle: null,
        targetHandle: null,
      },
    ],
  );
}

/**
 * Run a loop's downstream cascade. No-op when the loop is already
 * running (the button flips to Stop in that state).
 */
export async function startLoopRun(loopId: string): Promise<LoopRunSummary | null> {
  const runStore = useLoopRunStore.getState();
  if (runStore.running[loopId]) return null;
  runStore.start(loopId);

  try {
    const { nodes, connections, patchNode, canvasId } = useCanvasCoreStore.getState();
    const startCanvasId = canvasId;
    const sameCanvas = () => useCanvasCoreStore.getState().canvasId === startCanvasId;

    const loop = nodes.find((n) => asObj(n).id === loopId);
    const roundStart = Math.max(
      1,
      Number((asObj(loop ?? {}).data as { round_start?: number } | undefined)?.round_start ?? 1),
    );
    const order = topoSortPrompts(nodes, connections, {
      promptIdAllowlist: new Set(downstreamPrompts(loopId, nodes, connections)),
    }).order;
    const tailId = order[order.length - 1];

    return await runLoopCascade({
      loopId,
      nodes,
      connections,
      caller: resolveCaller(),
      handlers: {
        onStatusChange: (id, status, fields) => {
          if (!sameCanvas()) return;
          patchNode(id, { data: { run_status: status, ...fields } });
        },
      },
      shouldStop: () =>
        useLoopRunStore.getState().isStopRequested(loopId) || !sameCanvas(),
      onRoundComplete: (roundIndex, results) => {
        if (!tailId || !sameCanvas()) return;
        // Failed rounds keep their error on the prompt status — an empty
        // "Run N:" slot would just be noise.
        if (!results.every((r) => r.ok)) return;
        upsertRoundSlot({ loopId, tailId, roundIndex, roundStart, results });
      },
    });
  } finally {
    useLoopRunStore.getState().finish(loopId);
  }
}
