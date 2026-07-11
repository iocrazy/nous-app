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
}

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
      if (gen.kind === 'video' && gen.aspect) params.aspect = gen.aspect;

      const taskIds = await dispatchGenerations(deps.canvasId, {
        node_id: ctx.promptId,
        kind: gen.kind,
        prompt: ctx.body,
        model: gen.model ?? '',
        count: gen.kind === 'video' ? 1 : Math.max(1, Math.min(gen.count ?? 1, 8)),
        params,
        ...(ctx.source_url ? { source_url: ctx.source_url } : {}),
      });

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
          }),
        ),
      );

      // Per-item independence (Infinite semantics, P0-2): completed items
      // always land; failed siblings are reported alongside, never allowed
      // to throw away good results. The prompt only fails when NOTHING
      // completed with a url.
      const failed = tasks.filter((t) => t.phase !== 'completed');
      const urls = tasks
        .filter((t) => t.phase === 'completed')
        .map((t) => t.metadata?.result_url)
        .filter((u): u is string => Boolean(u));
      const firstError = failed[0]
        ? failed[0].error_msg || `generation ${failed[0].phase}`
        : null;
      if (urls.length === 0) {
        return {
          ok: false,
          text: '',
          error: firstError ?? 'generation completed without results',
        };
      }
      return {
        ok: true,
        text: urls.join('\n'),
        error: failed.length
          ? `${failed.length} of ${tasks.length} items failed: ${firstError}`
          : null,
        urls,
        media_kind: gen.kind,
      };
    } catch (err) {
      return {
        ok: false,
        text: '',
        error: err instanceof Error ? err.message : String(err),
      };
    }
  };
}
