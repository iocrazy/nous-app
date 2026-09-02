// features/canvas-core/smart/generationRunner.ts
//
// Generation-aware PromptCaller (Infinite-Canvas parity G4-F1). Wraps the
// text runner: prompts without gen settings pass straight through; image/
// video prompts dispatch count× backend tasks (G4-B1 — the DBOS queue is
// the concurrency governor) and poll them all to a terminal phase. All
// failures are in-band (ok=false), matching the runner contract — callers
// sequence rounds/cascades without try/catch.

import {
  dispatchGenerations,
  pollGeneration,
  PollStopped,
} from '../services/canvasGenerationService';
import type { PromptCaller, RunnerResult } from './runner';
import { isAutoRatio, measureRatio } from './autoRatio';

export interface GenerationRunnerDeps {
  canvasId: string | null;
  /** Poll tuning — tests tighten these. */
  pollIntervalMs?: number;
  pollTimeoutMs?: number;
  /** Mid-poll lifecycle (G4-F3): 'queued' while the engine still has the
   *  task in line (jimeng queue), 'running' once it starts. */
  onPhase?: (promptId: string, phase: 'queued' | 'running') => void;
  /** Cooperative stop (P0-4) — abandons in-flight polling; the runner
   *  returns a `stopped` result so the node goes back to idle. Receives
   *  the prompt id (P2-9) so callers with per-node batches can resolve
   *  which batch's stop flag applies. */
  shouldStop?: (promptId: string) => boolean;
  /** Placeholder lifecycle (P0-3): fired right after dispatch with the
   *  fan-out size — the caller shows N shimmer cells. taskIds (P1-13) let
   *  the caller persist the in-flight batch for reload resume.
   *
   *  `ratio` is the aspect the dispatch ACTUALLY sent, which is not the
   *  prompt's own value on the default image path: `'auto'` and an unset
   *  value both mean "follow the source", and only this side has measured
   *  it. The caller sizes the output slot's cells from it, so handing over
   *  the unresolved value would reserve a square box for a 16:9 result.
   *  `null` means the dispatch sent no ratio at all — nothing was
   *  measurable — and the caller should fall back rather than guess. */
  onDispatched?: (
    promptId: string,
    count: number,
    kind: 'image' | 'video',
    taskIds: string[],
    ratio: string | null,
  ) => void;
  /** First-done-first-shown (P0-3): fired as EACH task completes with a
   *  url — the caller replaces one shimmer cell. Errors fire with url=null;
   *  recoverable=true (P1-13) means the POLL broke, not the task — it may
   *  still finish server-side and can be re-queried by taskId. */
  onItemSettled?: (
    promptId: string,
    item: {
      url: string | null;
      kind: 'image' | 'video';
      taskId: string;
      recoverable?: boolean;
    },
  ) => void;
  /** What the backend could not honour (P4). Fires TWICE per run, and both
   *  are load-bearing:
   *
   *  - once with `[]` the moment the run starts, BEFORE dispatch — the
   *    previous run's answer stops describing this node the instant a new
   *    run does. Without it the node shows `running` beside a verdict about
   *    a finished run, and a dispatch that throws would leave that verdict
   *    up forever;
   *  - once at the terminal poll with the union across the fan-out — but
   *    ONLY if some task actually reported the field. A batch whose polls
   *    all broke observed nothing, and "we never got an answer" must not be
   *    written down as "nothing was dropped".
   *
   *  An empty union from a task that DID report is still fired: that is a
   *  real observation of a clean run. Fired for a failed run too — whether
   *  an image came out is a separate question from whether the request was
   *  honoured. */
  onDropped?: (promptId: string, knobs: string[]) => void;
}

/** Sentinel phase for tasks whose poll broke (network/timeout) — the task
 *  itself is NOT lost; it keeps running server-side (P1-13). */
const RECOVER_PHASE = '__recover__';

export function withGenerationRunner(
  base: PromptCaller,
  deps: GenerationRunnerDeps,
): PromptCaller {
  return async (ctx): Promise<RunnerResult> => {
    const gen = ctx.gen;
    if (!gen) return base(ctx);
    if (!deps.canvasId) {
      return {
        ok: false,
        text: '',
        error: 'generation requires an open canvas (no canvas id)',
      };
    }

    try {
      // A new run starts: whatever the LAST run reported was ignored no
      // longer describes this node. Cleared here rather than at the terminal
      // poll so the badge is never displayed as truth beside a `running`
      // status — and so a dispatch that throws below still clears it instead
      // of leaving a stale verdict up indefinitely.
      deps.onDropped?.(ctx.promptId, []);

      const params: Record<string, unknown> = {};
      if (gen.kind === 'image') {
        // `auto` (and an unset value) means "match the image feeding this
        // prompt". Resolved HERE rather than when the node was created: the
        // wired input can change afterwards, and a ratio frozen at creation
        // would quietly stop matching what the user sees.
        //
        // An explicit choice is never overridden, and an unmeasurable source
        // sends no ratio at all rather than a guess.
        if (isAutoRatio(gen.ratio)) {
          const followed = ctx.source_url ?? ctx.source_urls?.[0];
          if (followed) {
            const measured = await measureRatio(followed);
            if (measured) params.ratio = measured;
          }
        } else if (gen.ratio) {
          params.ratio = gen.ratio;
        }
      }
      if (gen.kind === 'image' && gen.quality) params.quality = gen.quality;
      if (gen.kind === 'image' && ctx.source_urls?.length)
        params.source_urls = ctx.source_urls;
      if (gen.kind === 'image' && gen.resolution)
        params.resolution = gen.resolution;
      if (gen.kind === 'video' && gen.aspect) params.aspect = gen.aspect;
      if (gen.kind === 'video' && gen.duration)
        params.duration = gen.duration;
      if (gen.kind === 'video' && gen.resolution)
        params.resolution = gen.resolution;
      if (gen.kind === 'video' && gen.video_mode)
        params.video_mode = gen.video_mode;
      if (gen.kind === 'video' && ctx.source_urls?.length)
        params.source_urls = ctx.source_urls;
      if (ctx.entity_ref) {
        // CC5 asset backlink: params land verbatim in generated_media.params,
        // which the library asset strips query by entity.
        params.entity_kind = ctx.entity_ref.kind;
        params.entity_id = ctx.entity_ref.id;
      }

      // IC 分隔符拆分: each split item dispatches independently — one
      // prompt node fans out into N generations whose results all land on
      // this node's output slot.
      const promptItems =
        ctx.split_prompts && ctx.split_prompts.length > 1
          ? ctx.split_prompts
          : [ctx.body];
      const perItemCount =
        gen.kind === 'video' ? 1 : Math.max(1, Math.min(gen.count ?? 1, 8));
      const taskIdBatches = await Promise.all(
        promptItems.map((item) =>
          dispatchGenerations(deps.canvasId, {
            node_id: ctx.promptId,
            kind: gen.kind,
            prompt: item,
            model: gen.model ?? '',
            count: perItemCount,
            params,
            ...(ctx.source_url ? { source_url: ctx.source_url } : {}),
          }),
        ),
      );
      const taskIds = taskIdBatches.flat();
      // Read off `params`, not off `gen`: this is the request as sent, after
      // auto-resolution. Image runs carry it as `ratio`, video runs as
      // `aspect`; either is absent when nothing was knowable.
      const dispatchedRatio =
        (params.ratio as string | undefined) ?? (params.aspect as string | undefined) ?? null;
      deps.onDispatched?.(
        ctx.promptId,
        taskIds.length,
        gen.kind,
        taskIds,
        dispatchedRatio,
      );

      // Fold N tasks' phases into one prompt-level signal: queued while
      // EVERYTHING is still in line, running once anything starts.
      let lastPhase: 'queued' | 'running' | null = null;
      const emit = (phase: 'queued' | 'running') => {
        if (phase !== lastPhase) {
          lastPhase = phase;
          deps.onPhase?.(ctx.promptId, phase);
        }
      };
      const tasks = await Promise.all(
        taskIds.map((id) =>
          pollGeneration(id, {
            intervalMs: deps.pollIntervalMs,
            timeoutMs: deps.pollTimeoutMs,
            onTick: (t) => emit(t.phase === 'queued' ? 'queued' : 'running'),
            shouldStop: deps.shouldStop
              ? () => deps.shouldStop!(ctx.promptId)
              : undefined,
          }).then(
            (task) => {
              // First-done-first-shown (P0-3): surface each item the moment
              // its own poll settles instead of waiting for the whole batch.
              const url =
                task.phase === 'completed' ? task.metadata?.result_url ?? null : null;
              deps.onItemSettled?.(ctx.promptId, { url, kind: gen.kind, taskId: id });
              return task;
            },
            (err: unknown) => {
              // Cooperative stop aborts the whole run — rethrow.
              if (err instanceof PollStopped) throw err;
              // A broken POLL is not a failed TASK (P1-13): the backend keeps
              // running it. Mark the item recoverable and — crucially — do
              // NOT reject the shared Promise.all, which would throw away
              // every sibling's completed result.
              deps.onItemSettled?.(ctx.promptId, {
                url: null,
                kind: gen.kind,
                taskId: id,
                recoverable: true,
              });
              return {
                phase: RECOVER_PHASE,
                error_msg: err instanceof Error ? err.message : String(err),
              };
            },
          ),
        ),
      );

      // What the request asked for that the provider could not do (P4).
      // Read at the SAME terminal point as result_url, from the same
      // metadata: reading one and not the other is exactly how this repo
      // has three times shipped a backend field no frontend ever consumed.
      // Union rather than last-writer-wins — a fan-out is one user action,
      // and a knob dropped by any of its items was dropped for the run.
      const droppedUnion: string[] = [];
      let observed = false;
      for (const task of tasks) {
        const knobs = 'metadata' in task ? task.metadata?.dropped_knobs : undefined;
        if (!Array.isArray(knobs)) continue;
        observed = true;
        for (const knob of knobs) {
          if (typeof knob === 'string' && !droppedUnion.includes(knob))
            droppedUnion.push(knob);
        }
      }
      // Nobody reported: every poll broke (RECOVER_PHASE carries no metadata
      // at all), or the rows predate the field. `[]` here would turn "we
      // never got an answer" into "nothing was dropped" — the negative
      // result this repo files under empty-output-is-not-a-negative-result.
      // Staying silent leaves the dispatch-time clear standing, which reads
      // as "unknown", which is what it is.
      if (observed) deps.onDropped?.(ctx.promptId, droppedUnion);

      // Per-item independence (Infinite semantics, P0-2): completed items
      // always land; failed siblings are reported alongside, never allowed
      // to throw away good results. The prompt only fails when NOTHING
      // completed with a url.
      const recovers = tasks.filter((t) => t.phase === RECOVER_PHASE);
      const failed = tasks.filter(
        (t) => t.phase !== 'completed' && t.phase !== RECOVER_PHASE,
      );
      const urls = tasks
        .filter((t) => t.phase === 'completed')
        .map((t) => ('metadata' in t ? t.metadata?.result_url : undefined))
        .filter((u): u is string => Boolean(u));
      const firstError = failed[0]
        ? failed[0].error_msg || `generation ${failed[0].phase}`
        : recovers[0]
          ? `lost track of ${recovers.length} task${recovers.length > 1 ? 's' : ''} — not lost, re-query from the output node`
          : null;
      if (urls.length === 0) {
        return {
          ok: false,
          text: '',
          error: firstError ?? 'generation completed without results',
          media_kind: gen.kind,
        };
      }
      const lostCount = failed.length + recovers.length;
      return {
        ok: true,
        text: urls.join('\n'),
        error: lostCount
          ? `${lostCount} of ${tasks.length} items failed: ${firstError}`
          : null,
        urls,
        media_kind: gen.kind,
      };
    } catch (err) {
      if (err instanceof PollStopped) {
        return {
          ok: false,
          stopped: true,
          text: '',
          error: 'stopped by user',
          media_kind: gen.kind,
        };
      }
      return {
        ok: false,
        text: '',
        error: err instanceof Error ? err.message : String(err),
        media_kind: gen.kind,
      };
    }
  };
}
