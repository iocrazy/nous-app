// features/canvas-core/smart/genResume.ts
//
// Broken-connection recovery (P1-13 — Infinite's resumeSmartPendingTasks):
// every dispatched generation batch persists its task ids on the prompt
// node (data.gen_tasks → nodes_json → survives reload); on canvas load the
// pending batches re-attach polling and land through the normal slot
// channel. Recover-marked tasks (poll broke, task not lost) re-query once
// from the slot overlay. loopRun discipline throughout: canvasId snapshot
// guard on every write.
//
// Shot nodes (storyboard canvas epic Task 3, fix-round-2) get the SAME
// re-attach-by-task-id treatment as prompt nodes, just singular
// (ShotNodeData.gen_task_id — one task at a time, not a gen_tasks batch):
// dispatch time persists the task id (ShotNodeView.handleGenerate), and a
// stranded shot_status==='generating' with a task id re-attaches polling
// here (resumeShotTask) instead of guessing the outcome. Only a shot with
// NO task id (crashed in the dispatch-request window, before the id could
// be persisted) falls back to reconciling from the already-persisted
// image_url mirror — see the 'shot' branch inside resumePendingGenerations
// below.

import {
  cancelGeneration,
  getGeneration,
  pollGeneration,
} from '../services/canvasGenerationService';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import {
  appendGenerationResults,
  markGenerationRecover,
  resolveGenerationRecover,
  settleGenerationSlot,
} from './genSlots';
import type { OutputKind } from './types';

export interface PendingGenTask {
  task_id: string;
  kind: 'image' | 'video';
}

const asObj = (n: unknown) => n as Record<string, unknown>;

const NOT_LOST_ERROR =
  'lost track of the run — tasks are not lost, re-query from the output node';
const RELOAD_ERROR = 'interrupted by page reload';

/** Poll tuning seam for tests. */
export const resumePollTuning: { intervalMs?: number; timeoutMs?: number } = {};

function pendingTasksOf(node: unknown): PendingGenTask[] {
  const data = asObj(node).data as { gen_tasks?: PendingGenTask[] } | undefined;
  return Array.isArray(data?.gen_tasks) ? data.gen_tasks : [];
}

/** Dispatch time: remember the in-flight batch on the prompt node. */
export function persistPendingGenTasks(
  promptId: string,
  taskIds: string[],
  kind: 'image' | 'video',
): void {
  useCanvasCoreStore.getState().patchNode(promptId, {
    data: { gen_tasks: taskIds.map((task_id): PendingGenTask => ({ task_id, kind })) },
  });
}

/** One task settled (result, failure, or recover mark) — forget it. */
export function prunePendingGenTask(promptId: string, taskId: string): void {
  const store = useCanvasCoreStore.getState();
  const prompt = store.nodes.find((n) => asObj(n).id === promptId);
  if (!prompt) return;
  const rest = pendingTasksOf(prompt).filter((t) => t.task_id !== taskId);
  store.patchNode(promptId, { data: { gen_tasks: rest } });
}

/** Run settled as a whole (success, failure, or stop) — forget the batch. */
export function clearPendingGenTasks(promptId: string): void {
  useCanvasCoreStore.getState().patchNode(promptId, { data: { gen_tasks: [] } });
}

/**
 * Stop pressed (P1-1): really cancel every in-flight generation task on this
 * prompt node server-side, so the DBOS workflow stops burning provider quota
 * instead of just abandoning the frontend poll. Best-effort — a failed cancel
 * only logs; the poll is abandoned regardless via the batch's shouldStop.
 */
export async function cancelPendingGenTasks(promptId: string): Promise<void> {
  const store = useCanvasCoreStore.getState();
  const prompt = store.nodes.find((n) => asObj(n).id === promptId);
  if (!prompt) return;
  await Promise.all(
    pendingTasksOf(prompt).map((t) =>
      cancelGeneration(t.task_id).catch((err: unknown) => {
        console.error('[canvas] cancel generation failed:', t.task_id, err);
      }),
    ),
  );
}

/**
 * Re-query one recover-marked task from the slot overlay (single shot,
 * Infinite's 查询结果): completed lands its url, terminal failure burns
 * into the failed count, anything else stays re-queryable.
 */
export async function requeryRecoverTask(
  promptId: string,
  taskId: string,
): Promise<'completed' | 'failed' | 'pending'> {
  const startCanvasId = useCanvasCoreStore.getState().canvasId;
  const sameCanvas = () => useCanvasCoreStore.getState().canvasId === startCanvasId;
  try {
    const task = await getGeneration(taskId);
    if (!sameCanvas()) return 'pending';
    if (task.phase === 'completed') {
      const url = task.metadata?.result_url ?? null;
      const kind = (task.metadata?.media_kind as OutputKind) ?? 'image';
      resolveGenerationRecover(promptId, taskId, { url, kind });
      return url ? 'completed' : 'failed';
    }
    if (['failed', 'cancelled', 'lost'].includes(task.phase)) {
      resolveGenerationRecover(promptId, taskId, { url: null, kind: 'image' });
      return 'failed';
    }
    return 'pending';
  } catch (err) {
    // Query itself broke — the task stays re-queryable.
    console.error('recover re-query failed', err);
    return 'pending';
  }
}

async function resumePromptTasks(
  promptId: string,
  tasks: PendingGenTask[],
  sameCanvas: () => boolean,
): Promise<void> {
  const patch = (fields: Record<string, unknown>) => {
    if (sameCanvas()) {
      useCanvasCoreStore.getState().patchNode(promptId, { data: fields });
    }
  };
  // Until the first tick reports otherwise, the persisted run_status stands
  // — a still-queued batch must not fake 'running' (same fold as the live
  // runner's onPhase).
  let lastPhase: 'queued' | 'running' | null = null;
  const emit = (phase: 'queued' | 'running') => {
    if (phase !== lastPhase) {
      lastPhase = phase;
      patch({ run_status: phase });
    }
  };

  let landed = 0;
  let recovered = 0;
  let firstError: string | null = null;
  await Promise.all(
    tasks.map(async (t) => {
      try {
        const task = await pollGeneration(t.task_id, {
          intervalMs: resumePollTuning.intervalMs,
          timeoutMs: resumePollTuning.timeoutMs,
          onTick: (live) => emit(live.phase === 'queued' ? 'queued' : 'running'),
        });
        if (!sameCanvas()) return;
        const url =
          task.phase === 'completed' ? task.metadata?.result_url ?? null : null;
        if (url) {
          appendGenerationResults(promptId, [url], t.kind);
          landed += 1;
        } else {
          settleGenerationSlot(promptId, { failed: 1 });
          firstError ??= task.error_msg || `generation ${task.phase}`;
        }
      } catch {
        if (!sameCanvas()) return;
        markGenerationRecover(promptId, t.task_id, t.kind);
        recovered += 1;
      } finally {
        if (sameCanvas()) prunePendingGenTask(promptId, t.task_id);
      }
    }),
  );
  if (!sameCanvas()) return;
  if (landed > 0) {
    patch({ run_status: 'succeeded', run_error: firstError });
  } else {
    patch({
      run_status: 'failed',
      run_error: recovered > 0 ? NOT_LOST_ERROR : firstError ?? NOT_LOST_ERROR,
    });
  }
}

/**
 * Re-attach polling for one in-flight shot generation (fix-round-2, singular
 * counterpart to `resumePromptTasks` — a shot dispatches exactly one task).
 * `sameCanvas()` gates every write, same discipline as the prompt path: a
 * canvas switch (or unmount) mid-poll must not land a stale patch onto
 * whatever node now has this id in the new document.
 */
async function resumeShotTask(
  shotNodeId: string,
  taskId: string,
  sameCanvas: () => boolean,
): Promise<void> {
  const patch = (fields: Record<string, unknown>) => {
    if (sameCanvas()) {
      useCanvasCoreStore.getState().patchNode(shotNodeId, { data: fields });
    }
  };
  try {
    const task = await pollGeneration(taskId, {
      intervalMs: resumePollTuning.intervalMs,
      timeoutMs: resumePollTuning.timeoutMs,
    });
    const url = task.phase === 'completed' ? (task.metadata?.result_url ?? null) : null;
    if (url) {
      patch({ image_url: url, shot_status: 'done', gen_task_id: null });
    } else {
      patch({ shot_status: 'failed', gen_task_id: null });
    }
  } catch (err) {
    // Poll broke (network/timeout) — the task itself may still be running
    // server-side, but ShotNodeData has no output-slot-style recover
    // channel to re-query later, so fail safe: 'failed' unblocks the
    // button for a one-click retry rather than leaving it stuck forever.
    console.error('[genResume] shot re-poll failed:', shotNodeId, taskId, err);
    patch({ shot_status: 'failed', gen_task_id: null });
  }
}

/** Re-entrancy guard: StrictMode double-mounts (and rapid nav loops) must
 *  not attach two polls per task — that would land every result twice. */
const resuming = new Set<string>();

/**
 * Canvas load (and every app-internal nav back into an already-mounted
 * canvas — CanvasPage re-fires this on every loadStatus→'ready', which
 * includes navigating away and back without a full reload): re-attach
 * polling for every prompt that still has an in-flight batch, reset
 * prompts stranded in queued/running with no batch to recover (their
 * runner died with the previous page), and re-attach polling for any shot
 * node with a persisted `gen_task_id` (fix-round-2) — falling back to a
 * local-mirror reconcile only when no task id was ever persisted (truly
 * stranded, not just unresumed).
 */
export async function resumePendingGenerations(): Promise<void> {
  const store = useCanvasCoreStore.getState();
  const startCanvasId = store.canvasId;
  if (!startCanvasId || resuming.has(startCanvasId)) return;
  const sameCanvas = () => useCanvasCoreStore.getState().canvasId === startCanvasId;

  resuming.add(startCanvasId);
  try {
    const resumes: Array<Promise<void>> = [];
    for (const node of store.nodes) {
      const nodeType = asObj(node).type;
      const id = String(asObj(node).id);
      if (nodeType === 'prompt') {
        const data = (asObj(node).data ?? {}) as {
          run_status?: string;
          gen_tasks?: PendingGenTask[];
        };
        const tasks = pendingTasksOf(node);
        if (tasks.length > 0) {
          resumes.push(resumePromptTasks(id, tasks, sameCanvas));
        } else if (data.run_status === 'running' || data.run_status === 'queued') {
          // Text prompts (or gen runs that never persisted) can't be resumed —
          // stop them looking alive forever.
          store.patchNode(id, {
            data: { run_status: 'failed', run_error: RELOAD_ERROR },
          });
        }
      } else if (nodeType === 'shot') {
        const data = (asObj(node).data ?? {}) as {
          shot_status?: string | null;
          image_url?: string | null;
          gen_task_id?: string | null;
        };
        if (data.shot_status !== 'generating') continue;
        // Three-way reconcile (fix-round-2 — a bare image_url check alone
        // is a data race: an in-flight dispatch/poll chain from BEFORE the
        // nav-away is still running (nothing cancels it), so re-attaching
        // by task id is the only way to avoid racing a stale 'failed'
        // against that live chain's own eventual patch).
        if (data.gen_task_id) {
          // A task id was persisted — re-attach polling by id, same
          // treatment as a prompt's gen_tasks batch (singular here).
          resumes.push(resumeShotTask(id, data.gen_task_id, sameCanvas));
        } else if (data.image_url) {
          // No task id, but the frame already landed — a prior poll (this
          // tab or another) finished and patched before the task id got
          // cleared; don't lie about a finished result.
          store.patchNode(id, { data: { shot_status: 'done' } });
        } else {
          // No task id AND no image_url: truly stranded — the dispatch
          // request itself never got far enough to persist a task id
          // (browser died in that narrow window). Drop to 'failed' so
          // Generate re-enables and becomes retryable.
          store.patchNode(id, { data: { shot_status: 'failed' } });
        }
      }
    }
    await Promise.all(resumes);
  } finally {
    resuming.delete(startCanvasId);
  }
}
