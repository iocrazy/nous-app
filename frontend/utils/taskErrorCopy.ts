// One decision, in one place: what a failed task's error block should SAY.
//
// WHY THIS EXISTS
// Two catalogs already existed and neither reached Task Center:
//
//   errorCatalog.resolveTaskError  — reads the structured `metadata.error_code`
//     the backend classifies (PROVIDER_RATE_LIMIT, PROVIDER_AUTH, …) and
//     returns translated, actionable copy. Wired into ONE component
//     (resources/PromptSection).
//   humanizeTaskError              — pattern-matches the raw English string.
//     Wired into Task Center, which is where users actually look.
//
// So on 2026-08-19 an ai_summary failure whose `metadata.error_code` said
// PROVIDER_RATE_LIMIT was rendered in Task Center from `error_msg` alone —
// and `error_msg` was the trigger's "Workflow failed — open detail to see the
// exception." placeholder. The backend had classified the failure correctly
// and the UI showed a shrug, because nothing joined the two halves.
//
// Rule: a structured code, when the backend supplied one, beats guessing from
// prose — it is translated and carries a fix ("switch this task to a different
// model in Settings → AI"). With no code, fall through to the prose reader.
// Either way the caller keeps showing the raw text under "Details", so nothing
// the engine reported is hidden.

import { resolveTaskError, type TranslateFn } from './errorCatalog';
import { humanizeTaskError } from './humanizeTaskError';

export interface TaskErrorCopy {
  /** Headline for the error block. Never empty. */
  message: string;
  /** What to do about it, when we know. */
  hint?: string;
  /** Present only when the backend supplied a structured code. */
  code?: string;
  /**
   * Free-form text the failing party wrote for the user to READ — today, the
   * model's own words when it declined a prompt (why, and the rewrite it
   * suggests). Present only when there is some: an empty string here would
   * render a blank explanation panel, which says "it had nothing to say"
   * rather than "this daemon is too old to tell you".
   *
   * It rides `metadata`, never `error_msg`, and that is not a style choice:
   * `error_msg` is derived from the pickled exception by
   * `public.dbos_error_to_text()` (migration 219), which splits on every byte
   * >= 0x80 and keeps the longest chunk — a Chinese sentence arrives there as
   * a fragment. jsonb keeps it byte-for-byte.
   */
  detail?: string;
}

/** `metadata.failure.detail`, when it is really there and really a string.
 *  The metadata comes off the wire; shape is not a promise. */
function failureDetail(metadata: Record<string, unknown> | null | undefined): string | undefined {
  const failure = (metadata ?? {})['failure'];
  if (!failure || typeof failure !== 'object' || Array.isArray(failure)) return undefined;
  const detail = (failure as Record<string, unknown>)['detail'];
  return typeof detail === 'string' && detail.trim() ? detail : undefined;
}

export function taskErrorCopy(
  metadata: Record<string, unknown> | null | undefined,
  errorMsg?: string | null,
  t?: TranslateFn,
): TaskErrorCopy {
  const detail = failureDetail(metadata);
  const resolved = resolveTaskError(metadata, null, t);
  if (resolved.code) {
    return { message: resolved.title, hint: resolved.hint, code: resolved.code, ...(detail ? { detail } : {}) };
  }
  // No structured code: read the prose. Passing `null` as resolveTaskError's
  // rawError above is deliberate — its own raw fallback truncates to 160
  // chars and skips the pattern table, which is strictly worse here.
  const { message, hint } = humanizeTaskError(errorMsg);
  return { message, ...(hint ? { hint } : {}), ...(detail ? { detail } : {}) };
}
