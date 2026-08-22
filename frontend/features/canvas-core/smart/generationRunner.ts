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
   *  the caller persist the in-flight batch for reload resume. */
  onDispatched?: (
    promptId: string,
    count: number,
    kind: 'image' | 'video',
    taskIds: string[],
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
      const params: Record<string, unknown> = {};
      if (gen.kind === 'image' && gen.ratio) params.ratio = gen.ratio;
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

      const taskIds = await dispatchGenerations(deps.canvasId, {
        node_id: ctx.promptId,
        kind: gen.kind,
        prompt: ctx.body,
        model: gen.model ?? '',
        count: gen.kind === 'video' ? 1 : Math.max(1, Math.min(gen.count ?? 1, 8)),
        params,
        ...(ctx.source_url ? { source_url: ctx.source_url } : {}),
      });
      deps.onDispatched?.(ctx.promptId, taskIds.length, gen.kind, taskIds);

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
