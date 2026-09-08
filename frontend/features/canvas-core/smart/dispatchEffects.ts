// What happens the moment a generation is dispatched.
//
// The canvas starts runs from more than one place — the composer's
// Run/Cascade, the Run inside a prompt node, the attached panel's Run. They
// used to each wire their own dispatch handler, and they drifted: only the
// composer created the output slot up front, so a run started from a node
// left the canvas looking idle until results landed. Nothing failed; the work
// was simply invisible.
//
// Keeping the effect in one place is the point. A new entry point gets the
// whole behaviour by calling this, and `dispatchEffects.test.ts` scans the
// call sites so the next one cannot quietly do less.

import { persistPendingGenTasks } from './genResume';
import { beginGenerationSlot, settleRunTerminal } from './genSlots';

/**
 * Show the run on the canvas and make it survive a reload.
 *
 * - `beginGenerationSlot` puts the output node to the RIGHT of the prompt,
 *   wired to it, holding `count` shimmer cells — the run acquires a visible
 *   direction and destination the instant it starts.
 * - `persistPendingGenTasks` records the task ids on the prompt so a reload
 *   resumes the batch instead of stranding it.
 *
 * Safe to call again for a re-run: the slot is reused and its previous images
 * are archived to history rather than duplicated.
 */
export function onGenerationDispatched(
  promptId: string,
  count: number,
  // Narrower than OutputKind on purpose: this is exactly what the generation
  // runner emits, and `persistPendingGenTasks` accepts nothing wider.
  kind: 'image' | 'video',
  taskIds: string[],
  /** The aspect the dispatch actually sent, already auto-resolved. The slot
   *  sizes its cells from this; `null` (nothing was knowable) leaves it to
   *  fall back to the prompt's own value. */
  ratio: string | null = null,
): void {
  beginGenerationSlot(promptId, count, kind, ratio);
  persistPendingGenTasks(promptId, taskIds, kind);
}

/**
 * What happens the moment a generation run reaches a terminal status.
 *
 * Lives beside `onGenerationDispatched` for the same reason that one does:
 * the canvas ends runs from more than one place (composer, node Run, resume
 * after reload), and an entry point that ends a run without this leaves a
 * shimmer cell pulsing on a finished node. `dispatchEffects.test.ts` scans
 * the call sites so the next entry point cannot quietly do less.
 */
export function onGenerationTerminal(promptId: string): void {
  settleRunTerminal(promptId);
}
