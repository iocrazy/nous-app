// features/canvas-core/smart/regenerate.ts
//
// Regenerate from an output slot (Infinite parity G7): re-run the slot's
// source prompt with its CURRENT body/gen settings. Results land through
// the normal slot channel (upsertGenerationSlots → replace + history
// archive), statuses through the same patch channel as the composer.
//
// Store-wired like loopRun: the canvasId is snapshotted at start and every
// write is guarded — an in-flight regenerate must never write into another
// document after the user switches canvases.

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import {
  clearPendingGenTasks,
  persistPendingGenTasks,
  prunePendingGenTask,
} from './genResume';
import { markGenerationRecover, upsertGenerationSlots } from './genSlots';
import { resolveEntityRef } from './entityRef';
import {
  resolveEffectiveSourceUrl,
  resolveEffectiveSourceUrls,
} from './promptInputs';
import { regenKey, useRegenStore } from './regenStore';
import { withGenerationRunner } from './generationRunner';
import { createBackendRunner } from './runner.backend';
import {
  mockRunner,
  runSinglePrompt,
  type PromptCaller,
  type RunnerContext,
} from './runner';
import type { OutputKind, PromptNodeData } from './types';

const asObj = (n: unknown) => n as Record<string, unknown>;

/** Source prompt id for a generation slot, null for non-slot outputs. */
export function promptIdForOutput(outputNodeId: string): string | null {
  const node = useCanvasCoreStore
    .getState()
    .nodes.find((n) => asObj(n).id === outputNodeId);
  const data = asObj(node ?? {}).data as
    | { gen_slot?: { node_id?: string } }
    | undefined;
  return data?.gen_slot?.node_id ?? null;
}

function resolveCaller(canvasId: string | null): PromptCaller {
  const override = useRegenStore.getState().runnerOverride;
  if (override) return override;
  if (canvasId) {
    return withGenerationRunner(createBackendRunner({ canvasId }), {
      canvasId,
      // Reruns survive a reload too (P1-13): persist the batch, prune per
      // settled item, mark broken polls recoverable. Results still land via
      // the end-of-run upsert below — resume only takes over after refresh.
      onDispatched: (id, _count, kind, taskIds) =>
        persistPendingGenTasks(id, taskIds, kind),
      onItemSettled: (id, item) => {
        if (!item.url && item.recoverable) {
          markGenerationRecover(id, item.taskId, item.kind);
        }
        prunePendingGenTask(id, item.taskId);
      },
    });
  }
  return mockRunner;
}

/**
 * Re-run a prompt by id (G4-F3 failed-retry + the G7 slot Rerun). Results
 * land through the normal slot channel; true only when the run succeeded
 * AND its results were written into THIS canvas. `lockId` keys the
 * re-entrancy guard (the output node id for slot reruns, the prompt id
 * for direct retries).
 */
export async function rerunPrompt(
  promptId: string,
  lockId: string = promptId,
): Promise<boolean> {
  const { nodes, connections, patchNode, canvasId } = useCanvasCoreStore.getState();
  const regen = useRegenStore.getState();
  const key = regenKey(canvasId, lockId);
  if (regen.running[key]) return false;
  const prompt = nodes.find((n) => asObj(n).id === promptId);
  const data = asObj(prompt ?? {}).data as PromptNodeData | undefined;
  if (!data) return false;

  const startCanvasId = canvasId;
  const sameCanvas = () => useCanvasCoreStore.getState().canvasId === startCanvasId;

  const ctx: RunnerContext = {
    promptId,
    body: data.body,
    provider_slug: data.provider_slug,
    agent_id: data.agent_id,
    gen: data.gen ?? null,
    // Upstream durable image (G4-F3) — makes a retried/rerun prompt keep
    // its i2i / i2v input.
    source_url: resolveEffectiveSourceUrl(prompt!, nodes, connections),
    source_urls: resolveEffectiveSourceUrls(prompt!, nodes, connections),
    entity_ref: resolveEntityRef(promptId, nodes, connections),
  };

  regen.start(key);
  try {
    const result = await runSinglePrompt(ctx, resolveCaller(startCanvasId), {
      onStatusChange: (id, status, fields) => {
        if (!sameCanvas()) return;
        patchNode(id, { data: { run_status: status, ...fields } });
      },
    });
    if (sameCanvas() && result.media_kind) clearPendingGenTasks(promptId);
    if (!result.ok || !sameCanvas()) return false;
    if (result.urls?.length) {
      upsertGenerationSlots(
        promptId,
        result.urls,
        (result.media_kind as OutputKind) ?? 'image',
      );
    }
    return true;
  } finally {
    useRegenStore.getState().finish(key);
  }
}

/** Re-run the source prompt of an output slot (G7 Rerun affordance). */
export async function regenerateForOutput(outputNodeId: string): Promise<boolean> {
  const promptId = promptIdForOutput(outputNodeId);
  if (!promptId) return false;
  return rerunPrompt(promptId, outputNodeId);
}
