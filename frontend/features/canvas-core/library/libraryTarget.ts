// features/canvas-core/library/libraryTarget.ts
//
// The one predicate that decides what an aim COMMITS.
//
// A prompt node with `gen: null` is the Text kind (see `PromptNodeData` — the
// kind select writes `gen: null` for Text and a settings object for
// Image/Video). A text run sends `body` alone (`runner.backend.ts`), so a
// `manual_refs` entry written on one is dropped at dispatch with nothing said.
// The panel therefore inserts MENTION CHIPS into the body instead.
//
// Split into its own module because TWO files answer the question and they must
// not be able to disagree: `LibraryMediaPage` picks the primary action and the
// consequence line from it, and `LibraryPanel`'s target bar names what the aim
// will do. A bar promising references over a page that inserts chips is the
// same class of lie as a silent no-op — the user reads the bar, not the button.

import type { PromptNodeData } from '../smart/types';

/**
 * Does this live target take mention chips rather than reference images?
 *
 * `null` — the aim no longer resolves — is `false`: an aim with no node behind
 * it commits nothing at all, and both callers gate on their own
 * "is the aim live" test before reaching this one.
 */
export function isMentionTarget(targetData: PromptNodeData | null): boolean {
  return targetData !== null && !targetData.gen;
}
