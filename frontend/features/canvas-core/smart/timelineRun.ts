// features/canvas-core/smart/timelineRun.ts
//
// Timeline run wiring (G8-F1): dispatch ONE film task (the backend chains
// segments + concats), poll to terminal, land the durable /stream URL in a
// per-timeline video output slot (data.timeline_slot, reused on re-runs).
// loopRun discipline: canvasId snapshot guard, per-node re-entrancy store,
// history-free slot writes.

import { create } from 'zustand';

import {
  dispatchTimelineRun,
  pollGeneration,
} from '../services/canvasGenerationService';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';
import { createOutputNode } from './factories';
import type { TimelineNodeData } from './timeline';

const SLOT_OFFSET_X = 460; // timeline node is 420 wide — keep clear of it

interface TimelineRunState {
  running: Record<string, boolean>;
  /** Poll tuning seam for tests. */
  pollIntervalMs?: number;
  pollTimeoutMs?: number;
  start(id: string): void;
  finish(id: string): void;
  reset(): void;
}

export const useTimelineRunStore = create<TimelineRunState>((set) => ({
  running: {},
  start: (id) => set((s) => ({ running: { ...s.running, [id]: true } })),
  finish: (id) =>
    set((s) => {
      const { [id]: _done, ...rest } = s.running;
      return { running: rest };
    }),
  reset: () => set({ running: {} }),
}));

const asObj = (n: unknown) => n as Record<string, unknown>;

function upsertFilmSlot(timelineId: string, resultUrl: string): void {
  const store = useCanvasCoreStore.getState();
  const existing = store.nodes.find(
    (n) =>
      (asObj(n).data as { timeline_slot?: { node_id?: string } } | undefined)
        ?.timeline_slot?.node_id === timelineId,
  );
  if (existing) {
    store.patchNode(String(asObj(existing).id), {
      data: { kind: 'video', preview_url: resultUrl },
    });
    return;
  }
  const tl = store.nodes.find((n) => asObj(n).id === timelineId);
  if (!tl) return;
  const pos = (asObj(tl).position as { x: number; y: number }) ?? { x: 0, y: 0 };
  const base = createOutputNode(
    { kind: 'video', preview_url: resultUrl, preview_text: 'Timeline film' },
    { position: { x: pos.x + SLOT_OFFSET_X, y: pos.y } },
  );
  const slot = {
    ...base,
    data: {
      ...(base.data as unknown as Record<string, unknown>),
      timeline_slot: { node_id: timelineId },
    },
  } as CanvasNode;
  store.appendElementsNoHistory(
    [slot],
    [
      {
        id: `edge-${crypto.randomUUID()}`,
        source: timelineId,
        target: String(asObj(slot).id),
        sourceHandle: null,
        targetHandle: null,
      },
    ],
  );
}

/** Run a timeline node's film. True only when the film landed in THIS canvas. */
export async function startTimelineRun(timelineId: string): Promise<boolean> {
  const runStore = useTimelineRunStore.getState();
  if (runStore.running[timelineId]) return false;

  const { nodes, patchNode, canvasId } = useCanvasCoreStore.getState();
  const tl = nodes.find((n) => asObj(n).id === timelineId);
  const data = asObj(tl ?? {}).data as TimelineNodeData | undefined;
  const segments = data?.segments ?? [];
  if (!canvasId || segments.length === 0) return false;

  const startCanvasId = canvasId;
  const sameCanvas = () => useCanvasCoreStore.getState().canvasId === startCanvasId;
  const patchStatus = (fields: Record<string, unknown>) => {
    if (sameCanvas()) patchNode(timelineId, { data: fields });
  };

  runStore.start(timelineId);
  try {
    patchStatus({ run_status: 'queued', run_error: null });
    const taskId = await dispatchTimelineRun(startCanvasId, {
      node_id: timelineId,
      segments: segments.map((s) => ({ prompt: s.prompt, seconds: s.seconds })),
      model: data?.model ?? '',
      aspect: data?.aspect ?? '',
    });
    patchStatus({ run_status: 'running' });

    const state = useTimelineRunStore.getState();
    const task = await pollGeneration(taskId, {
      intervalMs: state.pollIntervalMs,
      timeoutMs: state.pollTimeoutMs,
    });

    if (task.phase !== 'completed') {
      patchStatus({
        run_status: 'failed',
        run_error: task.error_msg || `timeline run ${task.phase}`,
      });
      return false;
    }
    const resultUrl = task.metadata?.result_url;
    if (!resultUrl || !sameCanvas()) {
      if (!resultUrl) {
        patchStatus({ run_status: 'failed', run_error: 'film completed without a result url' });
      }
      return false;
    }
    upsertFilmSlot(timelineId, String(resultUrl));
    patchStatus({ run_status: 'succeeded', run_error: null });
    return true;
  } catch (err) {
    patchStatus({
      run_status: 'failed',
      run_error: err instanceof Error ? err.message : String(err),
    });
    return false;
  } finally {
    useTimelineRunStore.getState().finish(timelineId);
  }
}
