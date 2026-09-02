// What happens when a generation run has asset cards wired into it.
//
// Sibling of `droppedKnobs.ts`, and for the same reason: the canvas starts runs
// from four places (composer Run/Cascade, chain run, loop round, rerun/retry),
// each building its own generation runner. "Ask the bundle endpoint what this
// asset contributes, then report what it would not send" is exactly the kind of
// effect that gets wired at one site and forgotten at the other three — and a
// missing bundle looks identical to an asset with nothing to give. Keeping it in
// ONE function is the point; `GenerationRunnerDeps.assetInputs` is a REQUIRED
// field, so a fifth entry point cannot compile without deciding what to pass.
//
// This module is the seam between the pure composition (`promptInputs.ts`, which
// takes nodes/connections/scope as arguments and is fully testable) and the two
// ambient things a run actually needs: the live graph, and the team scope in the
// URL. Keeping the ambient reads here is what lets the composition stay pure.

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { currentCanvasScopeId } from './canvasScope';
import { resolveAssetInputs, type ComposedAssetInputs } from './promptInputs';

/** `(promptId, model) -> what the upstream asset cards add`. */
export type AssetInputsResolver = (
  promptId: string,
  model: string,
) => Promise<ComposedAssetInputs>;

/**
 * Report one run's bundle outcome onto the asset cards that fed it.
 *
 * Writes BOTH fields for EVERY contributing card, including the clean case: the
 * badge must describe the run it sits beside, so a run that dropped nothing has
 * to clear what an earlier one reported. `patchNode` no-ops on a node that was
 * deleted mid-run.
 */
export function markAssetBundleResult(inputs: ComposedAssetInputs): void {
  const { patchNode } = useCanvasCoreStore.getState();
  for (const c of inputs.contributions) {
    patchNode(c.nodeId, {
      data: { last_bundle_dropped: c.dropped, last_bundle_error: c.error },
    });
  }
}

/**
 * The production resolver every run site passes.
 *
 * Reads the graph from the store rather than closing over a render's copy: a
 * chain run resolves each prompt just before dispatch precisely because earlier
 * prompts in the same chain change the graph while it runs.
 */
export const resolveAssetInputsForRun: AssetInputsResolver = async (
  promptId,
  model,
) => {
  const { nodes, connections } = useCanvasCoreStore.getState();
  const inputs = await resolveAssetInputs(promptId, nodes, connections, {
    model,
    scopeId: currentCanvasScopeId(),
  });
  // Unconditional: a prompt with no asset cards has no contributions, so this
  // writes nothing. Guarding it would be one more place to get the "clear the
  // previous verdict" case wrong.
  markAssetBundleResult(inputs);
  return inputs;
};
