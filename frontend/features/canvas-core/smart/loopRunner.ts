// features/canvas-core/smart/loopRunner.ts
//
// From-loop cascade orchestration (Infinite-Canvas parity Phase 1 G3b).
// Ports Infinite's runSmartCascadeFromLoop semantics onto MediaHub's
// prompt-centric smart mode:
//
//   round indexes  = round_start, round_start+1, … (loopRoundIndexes)
//   per round body = [rotating loop prompt for the round] ⏎⏎ [prompt body],
//                    then 《计数》/《总数》/《进度》 injected (loopVars)
//   serial         = await one round after another; a failed round aborts
//                    the rest (matches Infinite's serial for-loop)
//   parallel       = worker pool over the round list, shared cursor,
//                    limit 6 by default (runSmartCascadeRoundsWithLimit);
//                    prompt statuses are AGGREGATED (one queued→running up
//                    front, one succeeded/failed at the end) because
//                    concurrent rounds share the same prompt nodes
//   stop           = cooperative — checked before each round / next pull;
//                    in-flight work finishes (Infinite's stopRequested)
//
// Pure orchestration: caller/handlers/stop are injected, no store access.

import type { CanvasConnection, CanvasNode } from '../types';
import { resolveAssetRef } from './assetRef';
import { promptBodyForRun, type MentionedAsset } from './mentionedAssets';
import {
  resolveEffectiveSourceUrl,
  resolveEffectiveSourceUrls,
  resolveSourceUrls,
} from './promptInputs';
import {
  clampBatchSize,
  injectLoopVariables,
  loopRoundIndexes,
  pickRotatingPrompt,
  sliceLoopImages,
} from './loopVars';
import {
  runPrompts,
  type PromptCaller,
  type RunHandlers,
  type RunnerContext,
  type RunnerResult,
} from './runner';
import { downstreamPrompts, topoSortPrompts } from './topology';
import type { LoopNodeData } from './types';

/** Infinite smartCascadeParallelLimit's engine-free default. */
export const DEFAULT_LOOP_PARALLEL_LIMIT = 6;

export interface LoopRunSummary {
  /** Total rounds the loop was configured to run. */
  rounds: number;
  /** Rounds that finished with every prompt succeeding. */
  completedRounds: number;
  /** True when a cooperative stop halted the run early. */
  stopped: boolean;
  /** True when any round failed (aborts remaining rounds). */
  failed: boolean;
}

export interface LoopRunOptions {
  loopId: string;
  nodes: CanvasNode[];
  connections: CanvasConnection[];
  caller: PromptCaller;
  handlers: RunHandlers;
  /** Cooperative stop — checked before each round / worker pull. */
  shouldStop?: () => boolean;
  /** Worker-pool size for parallel mode (default 6). */
  parallelLimit?: number;
  /** Fired after every finished round (success or failure) with its results. */
  onRoundComplete?: (index: number, results: RunnerResult[]) => void;
}

const asObj = (n: unknown) => n as Record<string, unknown>;

export async function runLoopCascade(opts: LoopRunOptions): Promise<LoopRunSummary> {
  const loop = opts.nodes.find(
    (n) => asObj(n).id === opts.loopId && asObj(n).type === 'loop',
  );
  const empty: LoopRunSummary = { rounds: 0, completedRounds: 0, stopped: false, failed: false };
  if (!loop) return empty;

  const data = (asObj(loop).data ?? {}) as LoopNodeData;
  const indexes = loopRoundIndexes({
    rounds: data.rounds ?? 1,
    roundStart: data.round_start ?? 1,
  });
  const total = indexes[indexes.length - 1];
  const loopPrompts = data.prompts ?? [];

  const promptIds = downstreamPrompts(opts.loopId, opts.nodes, opts.connections);
  const { order } = topoSortPrompts(opts.nodes, opts.connections, {
    promptIdAllowlist: new Set(promptIds),
  });
  const byId = new Map(opts.nodes.map((n) => [String(asObj(n).id), n]));
  const baseContexts: RunnerContext[] = [];
  for (const id of order) {
    const d = asObj(byId.get(id) ?? {}).data as
      | {
          body?: string;
          provider_slug?: string;
          agent_id?: string | null;
          gen?: RunnerContext['gen'];
          negative_body?: string | null;
          mentioned_assets?: MentionedAsset[];
        }
      | undefined;
    if (!d) continue;
    baseContexts.push({
      promptId: id,
      body: promptBodyForRun(d),
      provider_slug: d.provider_slug ?? '',
      agent_id: d.agent_id ?? null,
      // Without gen the generation runner passes the prompt to the TEXT
      // path — loop-driven image prompts silently ran as LLM calls (F3 fix).
      gen: d.gen ?? null,
      source_url: resolveEffectiveSourceUrl(
        byId.get(id)!,
        opts.nodes,
        opts.connections,
      ),
      source_urls: resolveEffectiveSourceUrls(
        byId.get(id)!,
        opts.nodes,
        opts.connections,
      ),
      negative_body: d.negative_body ?? null,
      asset_ref: resolveAssetRef(id, opts.nodes, opts.connections),
    });
  }
  if (baseContexts.length === 0) {
    return { ...empty, rounds: indexes.length };
  }

  // IC image-input (smartLoopInputImages): the loop resolves ITS OWN upstream
  // durable images once, then feeds a per-round slice as the downstream
  // generation's source. A `loop` node is not a durableImagesOf provider, so
  // this must be injected into the downstream contexts (not resolved by them).
  const loopImages = data.image_input
    ? resolveSourceUrls(opts.loopId, opts.nodes, opts.connections)
    : [];
  const batchSize = clampBatchSize(data.image_batch_size ?? 1);

  const roundContexts = (index: number): RunnerContext[] => {
    const loopPrompt = pickRotatingPrompt(loopPrompts, index);
    // Per-round image window; [0] is our single-source i2i pick (batch>1 =
    // follow-up). Falls back to each prompt's own source when the loop
    // supplies none — regression-safe for text-only loops.
    const sliced = loopImages.length
      ? sliceLoopImages(loopImages, index, batchSize)[0] ?? null
      : null;
    return baseContexts.map((ctx) => ({
      ...ctx,
      source_url: sliced ?? ctx.source_url,
      body: injectLoopVariables(
        loopPrompt ? `${loopPrompt}\n\n${ctx.body}` : ctx.body,
        { index, total },
      ),
    }));
  };

  const summary: LoopRunSummary = {
    rounds: indexes.length,
    completedRounds: 0,
    stopped: false,
    failed: false,
  };

  const parallel = data.mode === 'parallel' && indexes.length > 1;
  if (!parallel) {
    for (const index of indexes) {
      if (opts.shouldStop?.()) {
        summary.stopped = true;
        break;
      }
      const results = await runPrompts(roundContexts(index), opts.caller, opts.handlers);
      const ok = results.length === baseContexts.length && results.every((r) => r.ok);
      opts.onRoundComplete?.(index, results);
      if (!ok) {
        summary.failed = true;
        break;
      }
      summary.completedRounds += 1;
    }
    return summary;
  }

  // Parallel: aggregate the shared prompt statuses once around the pool.
  const now = opts.handlers.now ?? (() => new Date().toISOString());
  for (const ctx of baseContexts) {
    opts.handlers.onStatusChange(ctx.promptId, 'queued', {
      run_started_at: null,
      run_finished_at: null,
      run_error: null,
    });
  }
  for (const ctx of baseContexts) {
    opts.handlers.onStatusChange(ctx.promptId, 'running', { run_started_at: now() });
  }

  const silentHandlers: RunHandlers = {
    onStatusChange: () => {},
    onResult: opts.handlers.onResult,
    now: opts.handlers.now,
  };
  let cursor = 0;
  let firstError: string | null = null;
  const limit = Math.max(1, opts.parallelLimit ?? DEFAULT_LOOP_PARALLEL_LIMIT);
  const workers = Array.from(
    { length: Math.min(limit, indexes.length) },
    async () => {
      for (;;) {
        if (opts.shouldStop?.()) {
          summary.stopped = true;
          return;
        }
        if (summary.failed) return;
        const i = cursor;
        cursor += 1;
        if (i >= indexes.length) return;
        const index = indexes[i];
        const results = await runPrompts(roundContexts(index), opts.caller, silentHandlers);
        const ok = results.length === baseContexts.length && results.every((r) => r.ok);
        if (ok) {
          summary.completedRounds += 1;
        } else {
          summary.failed = true;
          firstError = results.find((r) => !r.ok)?.error ?? 'loop round failed';
        }
        opts.onRoundComplete?.(index, results);
      }
    },
  );
  await Promise.all(workers);

  const finishedAt = now();
  for (const ctx of baseContexts) {
    if (summary.failed) {
      opts.handlers.onStatusChange(ctx.promptId, 'failed', {
        run_finished_at: finishedAt,
        run_error: firstError,
      });
    } else if (summary.stopped && summary.completedRounds === 0) {
      // Stopped before anything ran — a green "succeeded" would lie.
      opts.handlers.onStatusChange(ctx.promptId, 'idle', {
        run_started_at: null,
        run_finished_at: null,
        run_error: null,
      });
    } else {
      opts.handlers.onStatusChange(ctx.promptId, 'succeeded', {
        run_finished_at: finishedAt,
        run_error: null,
      });
    }
  }
  return summary;
}
