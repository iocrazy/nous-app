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
import {
  assetReferenceUrl,
  resolveAssetInputs,
  type ComposedAssetInputs,
} from './promptInputs';

/** `(promptId, model) -> what the upstream asset cards add`. */
export type AssetInputsResolver = (
  promptId: string,
  model: string,
) => Promise<ComposedAssetInputs>;

/**
 * Report one run's bundle outcome onto whatever fed it.
 *
 * Two destinations, because there are two kinds of contributor:
 *
 *   a wired CARD    reports on itself (`last_bundle_*`), where the user ticked
 *                   the boxes that produced the answer;
 *   an @-MENTION    has no card, so the whole run's mention outcome is
 *                   aggregated onto the PROMPT node (`last_mention_*`) — the
 *                   only place a user can see it.
 *
 * Writes for EVERY contributor including the clean case: the badge must
 * describe the run it sits beside, so a run that dropped nothing has to clear
 * what an earlier one reported. Mentions are written even when there are none,
 * for exactly the same reason — deleting the last chip must clear the badge.
 * `patchNode` no-ops on a node that was deleted mid-run.
 */
export function markAssetBundleResult(
  inputs: ComposedAssetInputs,
  promptId: string,
): void {
  const { patchNode } = useCanvasCoreStore.getState();
  const mentionDropped: Array<{ url: string; reason: string }> = [];
  const mentionErrors: string[] = [];
  for (const c of inputs.contributions) {
    if (c.source === 'mention' || c.nodeId === null) {
      for (const d of c.dropped) {
        mentionDropped.push({ url: assetReferenceUrl(d.resource_id), reason: d.reason });
      }
      if (c.error) mentionErrors.push(c.error);
      continue;
    }
    patchNode(c.nodeId, {
      data: { last_bundle_dropped: c.dropped, last_bundle_error: c.error },
    });
  }
  patchNode(promptId, {
    data: {
      last_mention_dropped: mentionDropped,
      // Joined rather than kept per-asset: the badge names a count and a
      // reason, and one line per failed mention would push the run status off
      // the card. The tooltip carries all of them.
      last_mention_error: mentionErrors.length > 0 ? mentionErrors.join('\n') : null,
    },
  });
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
  // Unconditional: a prompt with no asset cards has no card contributions, so
  // this writes nothing to any card — but it still clears the prompt's own
  // mention verdict. Guarding it would be one more place to get the "clear the
  // previous verdict" case wrong.
  markAssetBundleResult(inputs, promptId);
  return inputs;
};
