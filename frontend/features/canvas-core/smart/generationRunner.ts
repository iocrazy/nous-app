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
      });

      const tasks = await Promise.all(
        taskIds.map((id) =>
          pollGeneration(id, {
            intervalMs: deps.pollIntervalMs,
            timeoutMs: deps.pollTimeoutMs,
          }),
        ),
      );

      const failed = tasks.find((t) => t.phase !== 'completed');
      if (failed) {
        return {
          ok: false,
          text: '',
          error: failed.error_msg || `generation ${failed.phase}`,
        };
      }
      const urls = tasks
        .map((t) => t.metadata?.result_url)
        .filter((u): u is string => Boolean(u));
      if (urls.length === 0) {
        return { ok: false, text: '', error: 'generation completed without results' };
      }
      return {
        ok: true,
        text: urls.join('\n'),
        error: null,
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
