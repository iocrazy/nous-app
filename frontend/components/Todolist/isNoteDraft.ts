/**
 * Frontend mirror of the backend's `/note` rule (comment_trigger.is_note_comment).
 *
 * Used ONLY to decide WHEN to re-ask the server for a fresh comment-trigger
 * preview — the moment a draft crosses the note boundary in either direction.
 * It is never the verdict itself: the chip renders exactly what the server's
 * preview endpoint returned, and the send path re-evaluates the body
 * server-side. If this mirror ever drifts from the backend rule the only
 * symptom is a redundant or missing refetch, never a wrong disclosure.
 *
 * Rule (verbatim from backend/app/services/issues/comment_trigger.py):
 *  - leading whitespace ignored;
 *  - then the literal `/note` (case-sensitive);
 *  - followed by whitespace or end of string (`/notex` is NOT a note).
 */
const NOTE_PREFIX = '/note';

export function isNoteDraft(body: string | null | undefined): boolean {
  if (!body) return false;
  const stripped = body.replace(/^\s+/, '');
  if (!stripped.startsWith(NOTE_PREFIX)) return false;
  const rest = stripped.slice(NOTE_PREFIX.length);
  return rest === '' || /^\s/.test(rest);
}
