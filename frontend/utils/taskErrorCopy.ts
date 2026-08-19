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
}

export function taskErrorCopy(
  metadata: Record<string, unknown> | null | undefined,
  errorMsg?: string | null,
  t?: TranslateFn,
): TaskErrorCopy {
  const resolved = resolveTaskError(metadata, null, t);
  if (resolved.code) {
    return { message: resolved.title, hint: resolved.hint, code: resolved.code };
  }
  // No structured code: read the prose. Passing `null` as resolveTaskError's
  // rawError above is deliberate — its own raw fallback truncates to 160
  // chars and skips the pattern table, which is strictly worse here.
  const { message, hint } = humanizeTaskError(errorMsg);
  return hint ? { message, hint } : { message };
}
