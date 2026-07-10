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
import { upsertGenerationSlots } from './genSlots';
import { useRegenStore } from './regenStore';
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
    return withGenerationRunner(createBackendRunner({ canvasId }), { canvasId });
  }
  return mockRunner;
}

/**
 * Re-run the source prompt of an output slot. Resolves true only when the
 * run succeeded AND its results were written into this canvas.
 */
export async function regenerateForOutput(outputNodeId: string): Promise<boolean> {
  const regen = useRegenStore.getState();
  if (regen.running[outputNodeId]) return false;

  const { nodes, patchNode, canvasId } = useCanvasCoreStore.getState();
  const promptId = promptIdForOutput(outputNodeId);
  if (!promptId) return false;
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
  };

  regen.start(outputNodeId);
  try {
    const result = await runSinglePrompt(ctx, resolveCaller(startCanvasId), {
      onStatusChange: (id, status, fields) => {
        if (!sameCanvas()) return;
        patchNode(id, { data: { run_status: status, ...fields } });
      },
    });
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
    useRegenStore.getState().finish(outputNodeId);
  }
}
