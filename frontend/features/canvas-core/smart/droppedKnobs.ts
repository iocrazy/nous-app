// What happens when a generation run finishes with knobs the backend could
// not honour.
//
// The canvas starts generation runs from four places (the composer's
// Run/Cascade, a chain run, a loop round, and a rerun/retry). Each builds its
// own generation runner, so "show what was ignored" is exactly the kind of
// thing that ends up wired at one site and missing at the other three —
// silently, because a missing badge looks identical to a clean run. Keeping
// the effect in one function is the point; `generationRunner.test.ts` scans
// the call sites so a fifth entry point cannot quietly do less.

import { useCanvasCoreStore } from '../store/canvasCoreStore';

/**
 * Record the last run's dropped knobs on the prompt node.
 *
 * Always writes, including the empty list: the badge must describe the run it
 * sits beside, so a clean re-run has to clear what an earlier one reported.
 * The node may be gone (deleted mid-run) — `patchNode` no-ops on a missing id.
 */
export function markDroppedKnobs(promptId: string, knobs: string[]): void {
  useCanvasCoreStore.getState().patchNode(promptId, {
    data: { last_dropped: knobs },
  });
}
