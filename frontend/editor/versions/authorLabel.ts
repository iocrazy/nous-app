/**
 * authorLabel — resolve a diff change's `actor` (a user uuid, 'copilot', or
 * null) to the short display string shown on its author chip. The backend
 * already batch-resolves real user uuids to usernames (`authors` map); this only
 * layers the two client-side specials on top: the AI author and "you".
 */

export interface AuthorLabelContext {
  /** The signed-in user's id — their changes read as "You" (i18n). */
  currentUserId?: string | null;
  /** Server-resolved uuid → username map from the diff response. */
  authors?: Record<string, string>;
  /** i18n translate fn (only `diffAuthorYou` / `diffAuthorAI` keys used). */
  t: (key: string) => string;
}

/** The op ledger writes 'copilot' as the actor for AI-authored changes. */
export const COPILOT_ACTOR = 'copilot';

/**
 * Return the chip label for `actor`, or `null` when there is nothing to show
 * (no actor recorded). Resolution order: current user → "You"; copilot → the AI
 * label; a server-resolved username; otherwise a short id fallback so an
 * unresolved uuid still reads as *someone* rather than a raw 36-char string.
 */
export function authorLabel(
  actor: string | null | undefined,
  { currentUserId, authors, t }: AuthorLabelContext,
): string | null {
  if (!actor) return null;
  if (currentUserId && actor === currentUserId) return t('editor.diffAuthorYou');
  if (actor === COPILOT_ACTOR) return t('editor.diffAuthorAI');
  const resolved = authors?.[actor];
  if (resolved) return resolved;
  return actor.length > 8 ? actor.slice(0, 8) : actor;
}
