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
import type { AssetInputsResolver } from './assetInputs';
import type { PromptCaller, RunnerResult } from './runner';
import type { DroppedRef } from './types';
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
   *  honoured.
   *
   *  `refs` is the second, ORTHOGONAL ledger (asset-library P4): references
   *  the backend could not resolve — a resource outside the run's scope, one
   *  with no image bytes, a url shape it does not serve. It travels beside
   *  `knobs` rather than inside it because a run can lose a knob, lose a
   *  reference, or both, and folding them together makes "which picture is
   *  missing" unanswerable. Both are read at the SAME terminal metadata read,
   *  so neither can quietly acquire a consumer the other lacks. */
  onDropped?: (promptId: string, knobs: string[], refs: DroppedRef[]) => void;
  /** What the upstream asset cards contribute to this run (asset-library P4).
   *
   *  REQUIRED, not optional. Every run path must answer the question, because
   *  the two answers are indistinguishable on screen: a site that forgot to
   *  wire it would dispatch a prompt whose asset card contributed nothing and
   *  look exactly like a card that had nothing to contribute. Production sites
   *  pass `resolveAssetInputsForRun`; tests that are not about assets pass
   *  `noAssetInputs`, which says so out loud.
   *
   *  Resolved per run, not cached: the bundle depends on the MODEL (nine
   *  references for codex, none for ark) and the model is a knob the user
   *  changes between runs. */
  assetInputs: AssetInputsResolver;
}

/** An explicit "this run has no asset composition" — for tests and for the
 *  headless/mock paths, so the absence is a decision in the source rather than
 *  a missing property. */
export const noAssetInputs: AssetInputsResolver = async () => ({
  reference_urls: [],
  prompt_prefix: '',
  negative: '',
  contributions: [],
});

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
      deps.onDropped?.(ctx.promptId, [], []);

      // What the upstream asset cards contribute, for THIS model. Resolved
      // before anything else is decided, because it changes both the reference
      // list the ratio is measured against and the prompt text that ships.
      // Reporting what the bundle would not send happens inside the resolver,
      // on the cards themselves — same "always write, including the clean
      // case" rule the prompt-node badge follows.
      const assets = await deps.assetInputs(ctx.promptId, gen.model ?? '');

      // Asset references LEAD. They are what the picture is OF, and both
      // ceilings that trim references — the frontend's none, the backend's
      // `reconcile` at `caps.max_refs` — trim from the tail, so anything put
      // ahead of them is a reference chosen for the model over a reference
      // chosen for the subject.
      const sourceUrls = [
        ...assets.reference_urls,
        ...(ctx.source_urls ?? []).filter(
          (u) => !assets.reference_urls.includes(u),
        ),
      ];

      const params: Record<string, unknown> = {};
      if (gen.kind === 'image') {
        // `auto` (and an unset value) means "match the image feeding this
        // prompt". Resolved HERE rather than when the node was created: the
        // wired input can change afterwards, and a ratio frozen at creation
        // would quietly stop matching what the user sees.
        //
        // An explicit choice is never overridden, and an unmeasurable source
        // sends no ratio at all rather than a guess.
        // `ctx.source_urls`, NOT the asset-prefixed list: `auto` means "match
        // the image feeding this prompt", and an asset's reference is a
        // subject, not a composition. Following it would let wiring a portrait
        // card silently turn a landscape board portrait.
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
      if (gen.kind === 'image' && sourceUrls.length)
        params.source_urls = sourceUrls;
      if (gen.kind === 'image' && gen.resolution)
        params.resolution = gen.resolution;
      if (gen.kind === 'video' && gen.aspect) params.aspect = gen.aspect;
      if (gen.kind === 'video' && gen.duration)
        params.duration = gen.duration;
      if (gen.kind === 'video' && gen.resolution)
        params.resolution = gen.resolution;
      if (gen.kind === 'video' && gen.video_mode)
        params.video_mode = gen.video_mode;
      if (gen.kind === 'video' && sourceUrls.length)
        params.source_urls = sourceUrls;
      // One negative for the request: the prompt node's own (loaded from a
      // prompt asset) plus every upstream card's, deduped. Whether the
      // provider takes one is NOT decided here — `GenerationRequest.reconcile`
      // drops it and names `negative` in `dropped_knobs`, which this node
      // already renders. Deciding it twice is how the two answers drift.
      const negative = [ctx.negative_body ?? '', assets.negative]
        .map((n) => n.trim())
        .filter((n, i, all) => n && all.indexOf(n) === i)
        .join('\n');
      if (negative) params.negative = negative;
      if (ctx.asset_ref) {
        // Asset-library provenance (P4, plan ruling H): params land verbatim in
        // generated_media.params, so a run carries the asset it was launched
        // from — and the outfit, which is a different picture of the same
        // character and therefore a separate answer to "what came from this".
        //
        // This REPLACED `entity_kind` / `entity_id`, whose ids were rows of
        // `_legacy_project_characters` / `_legacy_project_lib_entities` and
        // whose only reader had been gone since P3 Task 6. Both halves went
        // together; a negative test pins that neither key is written again.
        params.source_asset_id = ctx.asset_ref.asset_id;
        if (ctx.asset_ref.loadout_id) params.loadout_id = ctx.asset_ref.loadout_id;
      }

      // IC 分隔符拆分: each split item dispatches independently — one
      // prompt node fans out into N generations whose results all land on
      // this node's output slot.
      const bodies =
        ctx.split_prompts && ctx.split_prompts.length > 1
          ? ctx.split_prompts
          : [ctx.body];
      // The asset text leads EACH split item, not just the first: a split is N
      // independent generations of the same subject, so a prefix applied once
      // would describe only one of them.
      const promptItems = assets.prompt_prefix
        ? bodies.map((b) => [assets.prompt_prefix, b.trim()].filter(Boolean).join('\n'))
        : bodies;
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

      // What the request asked for that the provider could not do (P4).
      // Read at the SAME terminal point as result_url, from the same
      // metadata: reading one and not the other is exactly how this repo
      // has three times shipped a backend field no frontend ever consumed.
      // Union rather than last-writer-wins — a fan-out is one user action,
      // and a knob dropped by any of its items was dropped for the run.
      const droppedUnion: string[] = [];
      const refUnion: DroppedRef[] = [];
      let observed = false;
      for (const task of tasks) {
        const meta = 'metadata' in task ? task.metadata : undefined;
        const knobs = meta?.dropped_knobs;
        const refs = meta?.dropped_refs;
        // Either ledger reporting counts as an observation: a backend that
        // dropped a reference but no knob still answered the question.
        if (Array.isArray(knobs)) {
          observed = true;
          for (const knob of knobs) {
            if (typeof knob === 'string' && !droppedUnion.includes(knob))
              droppedUnion.push(knob);
          }
        }
        if (Array.isArray(refs)) {
          observed = true;
          for (const ref of refs) {
            if (!ref || typeof ref !== 'object') continue;
            const url = String((ref as DroppedRef).url ?? '');
            const reason = String((ref as DroppedRef).reason ?? '');
            if (!url) continue;
            if (refUnion.some((r) => r.url === url && r.reason === reason)) continue;
            refUnion.push({ url, reason });
          }
        }
      }
      // Nobody reported: every poll broke (RECOVER_PHASE carries no metadata
      // at all), or the rows predate the field. `[]` here would turn "we
      // never got an answer" into "nothing was dropped" — the negative
      // result this repo files under empty-output-is-not-a-negative-result.
      // Staying silent leaves the dispatch-time clear standing, which reads
      // as "unknown", which is what it is.
      if (observed) deps.onDropped?.(ctx.promptId, droppedUnion, refUnion);

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
