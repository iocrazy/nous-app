// features/canvas-core/library/promptPanelTarget.ts
//
// How a prompt card is NAMED wherever the panel has to say which one it means.
//
// A prompt card's own heading is the literal "Prompt", so the body's first line
// is the only thing a user would recognise it by. The 40-character cut and the
// fall back to "Prompt" when the body is empty are that name's whole definition.
//
// Split into its own module because THREE call sites answer the same question
// and they must not be able to disagree: both doors on a prompt card arm the
// panel's target bar with it, and the Prompts page seeds its save-a-template
// form with it. A card named one way in the bar and another in the form the bar
// opened is the kind of drift nobody reports and everybody works around.

import type { LibraryTarget } from './libraryStore';

/**
 * The display name for a prompt body.
 *
 * `fallback` is passed in rather than read here: this module has no `t()`, and
 * the caller already holds one. Every caller passes
 * `t('canvas.library.untitledPrompt', 'Prompt')`.
 *
 * A body of only whitespace still yields that whitespace, not the fallback —
 * `||` catches the empty string alone. That is deliberate: trimming here would
 * make the name disagree with the body the user can see in the card.
 */
export function titleFromBody(body: string | undefined, fallback: string): string {
  return (body ?? '').split('\n')[0].slice(0, 40) || fallback;
}

/** The panel target that aims at one prompt card, named by its body. */
export function promptPanelTarget(
  nodeId: string,
  body: string | undefined,
  fallback: string,
): LibraryTarget {
  return { nodeId, kind: 'prompt', title: titleFromBody(body, fallback) };
}
