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
//
// fix-round-3: re-attaching by task id is not enough on its own — the
// ORIGINAL `handleGenerate` closure that dispatched the task is a detached
// async chain nothing cancels on unmount, so it is STILL polling when an
// app-internal nav back into the same canvas re-fires this module and
// re-attaches a SECOND poller for the identical task id. Two pollers
// racing to patch the same node means a transient network hiccup on
// EITHER one can land a stale 'failed' over the other's correct 'done'
// (last-write-wins). `activeShotPolls` below is a module-level registry
// (survives remount, same lifecycle as the detached closure) that makes
// "is someone already polling this task" a real, checkable fact instead
// of an assumption.

import {
  cancelGeneration,
  getGeneration,
  pollGeneration,
} from '../services/canvasGenerationService';
import { ApiError } from '../../../services/apiClient';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { onGenerationTerminal } from './dispatchEffects';
import { markDroppedKnobs } from './droppedKnobs';
import type { DroppedRef } from './types';
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

// ---------------------------------------------------------------------------
// Shot generation poll registry (fix-round-3) — see the module doc comment
// above for why this exists. Key is `${canvasId}:${taskId}`, canvasId-
// qualified defensively in case a task id is ever reused across canvases
// (Snowflake-derived ids make this unlikely, not impossible enough to skip
// the qualifier). Two call sites share this registry: ShotNodeView's
// `handleGenerate` (the originating poller) and `resumeShotTask` below (the
// reconcile-triggered re-attach poller) — whichever claims a task id first
// owns landing its terminal patch; the other skips entirely rather than
// racing it.
// ---------------------------------------------------------------------------

const activeShotPolls = new Set<string>();

function shotPollKey(canvasId: string, taskId: string): string {
  return `${canvasId}:${taskId}`;
}

/** Claim a task id before starting to poll it. Call this the instant a
 *  dispatch/re-attach has a task id in hand, before the first `await`. */
export function registerActiveShotPoll(canvasId: string, taskId: string): void {
  activeShotPolls.add(shotPollKey(canvasId, taskId));
}

/** Release a claim once the poller is done with this task id — terminal
 *  state landed, OR the poller is giving up (must still release so a later
 *  reconcile pass isn't blocked forever). Always call from a `finally`. */
export function unregisterActiveShotPoll(canvasId: string, taskId: string): void {
  activeShotPolls.delete(shotPollKey(canvasId, taskId));
}

function isActiveShotPoll(canvasId: string, taskId: string): boolean {
  return activeShotPolls.has(shotPollKey(canvasId, taskId));
}

/** Test helper (mirrors `useRunToolActivity.ts`'s `settledCache` reset
 *  pattern) — clears the module-level registry between tests. */
export function __clearActiveShotPolls(): void {
  activeShotPolls.clear();
}

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
  let firstDetail: string | null = null;
  // Same read, same terminal point, same semantics as the live runner
  // (generationRunner). A resume is the OTHER way a generation reaches a
  // terminal phase, and reading `result_url` here while ignoring its
  // neighbour would put the badge back in the state this whole feature
  // exists to end: `last_dropped: []` from the dispatch-time clear is
  // persisted with the node, so silence after a resume would read as "the
  // request was honoured" for a run that dropped knobs.
  //
  // ⚠️ Known partial: only the tasks still PENDING at reload are polled here
  // (settled ones were pruned from gen_tasks), so a knob dropped by a
  // sibling that finished before the reload is unrecoverable — its task id
  // is gone. This under-reports rather than over-reports, and never reports
  // worse than the nothing it replaced.
  const droppedUnion: string[] = [];
  const refUnion: DroppedRef[] = [];
  let observedDropped = false;
  await Promise.all(
    tasks.map(async (t) => {
      try {
        const task = await pollGeneration(t.task_id, {
          intervalMs: resumePollTuning.intervalMs,
          timeoutMs: resumePollTuning.timeoutMs,
          onTick: (live) => emit(live.phase === 'queued' ? 'queued' : 'running'),
        });
        if (!sameCanvas()) return;
        const knobs = task.metadata?.dropped_knobs;
        if (Array.isArray(knobs)) {
          observedDropped = true;
          for (const knob of knobs) {
            if (typeof knob === 'string' && !droppedUnion.includes(knob))
              droppedUnion.push(knob);
          }
        }
        // The reference ledger, read at the same point for the same reason.
        // Carrying knobs across a reload and not references would put back
        // half the bug: the badge would describe this run's knobs beside a
        // CLEARED reference verdict, i.e. "every reference was used" about a
        // run where one was not.
        const refs = task.metadata?.dropped_refs;
        if (Array.isArray(refs)) {
          observedDropped = true;
          for (const ref of refs) {
            if (!ref || typeof ref !== 'object') continue;
            const url = String((ref as DroppedRef).url ?? '');
            const reason = String((ref as DroppedRef).reason ?? '');
            if (!url) continue;
            if (refUnion.some((r) => r.url === url && r.reason === reason)) continue;
            refUnion.push({ url, reason });
          }
        }
        const url =
          task.phase === 'completed' ? task.metadata?.result_url ?? null : null;
        if (url) {
          appendGenerationResults(promptId, [url], t.kind);
          landed += 1;
        } else {
          settleGenerationSlot(promptId, { failed: 1 });
          firstError ??= task.error_msg || `generation ${task.phase}`;
          const d = task.metadata?.failure?.detail;
          if (typeof d === 'string' && d.trim()) firstDetail ??= d;
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
  // Only when somebody actually reported the field: a resume batch whose
  // polls all broke observed nothing, and writing [] would turn that
  // non-answer into "nothing was dropped" (the catch branch below
  // deliberately does not set the flag, matching the runner).
  if (observedDropped) markDroppedKnobs(promptId, droppedUnion, refUnion);
  if (landed > 0) {
    patch({ run_status: 'succeeded', run_error: firstError, run_detail: firstDetail });
  } else {
    patch({
      run_status: 'failed',
      run_error: recovered > 0 ? NOT_LOST_ERROR : firstError ?? NOT_LOST_ERROR,
      run_detail: firstDetail,
    });
  }
  // A resumed batch ends the same way a live one does, so it owes the slot
  // the same settlement. Recover marks are NOT pending cells (they were
  // decremented when the mark was made) and keep their own re-query UI.
  onGenerationTerminal(promptId);
}

/**
 * Re-attach polling for one in-flight shot generation (fix-round-2, singular
 * counterpart to `resumePromptTasks` — a shot dispatches exactly one task).
 * `sameCanvas()` gates every write, same discipline as the prompt path: a
 * canvas switch (or unmount) mid-poll must not land a stale patch onto
 * whatever node now has this id in the new document.
 *
 * fix-round-3: guarded by `activeShotPolls` so this never attaches a SECOND
 * poller alongside one already running (most commonly the original
 * `handleGenerate` closure, still alive because nothing cancels it on
 * unmount). And its catch now distinguishes a server-confirmed terminal
 * failure (returned normally by `pollGeneration` as phase
 * failed/cancelled/lost — authoritative) from the POLL CHAIN itself
 * breaking (thrown — a network hiccup or the overall poll timeout, NOT
 * proof the task failed): the latter leaves `shot_status`/`gen_task_id`
 * untouched so a future reconcile pass retries from scratch, instead of
 * burning a real success/failure verdict on a transient blip.
 *
 * final review Important 2: a THROWN 404 specifically is carved out of that
 * "leave alone" rule — it means the task_tracking row was pruned (7-day
 * cleanup), which never resolves on retry, so treating it as transient would
 * retry-loop on every mount forever. See the catch block below.
 */
async function resumeShotTask(
  shotNodeId: string,
  taskId: string,
  canvasId: string,
  sameCanvas: () => boolean,
): Promise<void> {
  if (isActiveShotPoll(canvasId, taskId)) {
    // Someone else already owns this task id's terminal patch — most
    // likely the original dispatching closure, uninterrupted by the
    // unmount that triggered this reconcile pass. Do not attach a second
    // poller; it would only race the first one's write.
    return;
  }
  registerActiveShotPoll(canvasId, taskId);
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
    // A normal RETURN from pollGeneration means the task reached a
    // server-confirmed terminal phase — authoritative, regardless of
    // which poller observed it.
    const url = task.phase === 'completed' ? (task.metadata?.result_url ?? null) : null;
    if (url) {
      patch({ image_url: url, shot_status: 'done', gen_task_id: null });
    } else {
      patch({ shot_status: 'failed', gen_task_id: null });
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      // The task_tracking row itself is GONE — not a broken poll chain, a
      // terminal-but-unknown fact. `scheduled_cleanup.py` prunes terminal
      // task_tracking rows after 7 days (see its own doc comment), so an
      // old enough `gen_task_id` 404s forever: leaving state alone (the
      // transient-error branch below) would retry-loop on every mount
      // without end, and the shotSync in-flight guard (`isInFlight` —
      // shot_status==='generating' && gen_task_id set) would keep
      // permanently blocking the image_url/shot_status mirror refresh from
      // `reconcileShotNodes`, even after the backend finished the
      // generation and backfilled `script_shots` (final review Important
      // 2). Resolve from whatever is already locally known instead of
      // guessing: a frame already landed means done, otherwise this shot
      // never got a confirmable result and drops to failed so Generate
      // re-enables.
      const node = useCanvasCoreStore
        .getState()
        .nodes.find((n) => asObj(n).id === shotNodeId);
      const image_url =
        ((asObj(node).data ?? {}) as { image_url?: string | null }).image_url ?? null;
      patch({ gen_task_id: null, shot_status: image_url ? 'done' : 'failed' });
      return;
    }
    // The poll CHAIN broke (a getGeneration network error, or the overall
    // 30-minute timeout) — this is not the same fact as the task failing;
    // it may still be queued/running server-side. ShotNodeData has no
    // output-slot-style recover overlay to park it in the way prompt
    // generations do (that gap is left as-is for prompt — same ledger
    // entry, not this task's scope) — the shot-shaped equivalent is
    // simplest: leave shot_status/gen_task_id exactly as they were, so
    // the NEXT reconcile pass (another nav back into this canvas) sees
    // gen_task_id still set and retries re-attaching, rather than
    // reporting a false failure now.
    console.error(
      '[genResume] shot re-poll chain broke (state left alone for retry):',
      shotNodeId,
      taskId,
      err,
    );
  } finally {
    unregisterActiveShotPoll(canvasId, taskId);
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
          // resumeShotTask itself no-ops if this task id already has an
          // active poller (fix-round-3).
          resumes.push(resumeShotTask(id, data.gen_task_id, startCanvasId, sameCanvas));
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
