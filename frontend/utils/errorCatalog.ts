// Turn a failed task's structured error code into actionable, translated copy.
//
// WHY THIS EXISTS
// `task_tracking.error_msg` is written one-way by the DBOS engine (route C
// discipline, see CLAUDE.md), so the backend can't rewrite it into something
// a user can act on. A provider 401 during image captioning reached the UI as:
//
//   DBOSMaxStepRetriesExceeded: Step ai_caption_via_provider has exceeded
//   its maximum of 3 retries
//
// — which reads like a platform bug when the real fix was one field in
// Settings → AI. The backend now classifies the underlying exception into a
// stable code and merges it into the business-owned `metadata` jsonb
// (`backend/app/services/ai/error_catalog.py`); this module maps that code to
// i18n copy.
//
// RELATIONSHIP TO humanizeTaskError
// humanizeTaskError guesses from the raw English string and stays the right
// tool where no code exists (downloads, publishing, older rows). This module
// is the structured path: when the backend told us WHAT went wrong, we render
// translated, actionable copy instead of pattern-matching prose. No code
// present → we don't invent one; the caller falls back to the raw text.

/** Codes the backend's error_catalog can emit. Keep in sync with
 *  `ALL_ERROR_CODES` in `backend/app/services/ai/error_catalog.py` — a code
 *  missing here degrades to the raw-error fallback rather than breaking. */
export const AI_ERROR_CODES = [
  'PROVIDER_AUTH',
  'PROVIDER_RATE_LIMIT',
  'PROVIDER_QUOTA_CAP',
  'PROVIDER_UNREACHABLE',
  'PROVIDER_BAD_MODEL',
  'OUTPUT_PARSE',
  'TASK_TIMEOUT',
  'INTERNAL',
] as const;

export type AiErrorCode = (typeof AI_ERROR_CODES)[number];

/** English defaults, also the i18n fallback when a locale lacks the key. */
const DEFAULTS: Record<AiErrorCode, { title: string; hint: string }> = {
  PROVIDER_AUTH: {
    title: 'The AI provider rejected the credentials',
    hint: 'Open Settings → AI and check the API key for the model assigned to this task.',
  },
  PROVIDER_RATE_LIMIT: {
    title: 'The AI provider is rate-limiting',
    hint: 'Wait a moment and retry, or switch this task to a different model in Settings → AI.',
  },
  // Distinct from RATE_LIMIT on purpose: this is a cap configured on the
  // provider account, so "wait a moment" is wrong advice — it never clears
  // by itself. Backend keeps this ordered above the generic 429 rule.
  PROVIDER_QUOTA_CAP: {
    title: 'The provider account has hit its configured limit for this model',
    hint: 'Raise the limit in the provider console, or point this task at another model in Settings → AI.',
  },
  PROVIDER_UNREACHABLE: {
    title: "Couldn't reach the AI provider",
    hint: 'Check the provider endpoint in Settings → AI — if it is self-hosted, make sure it is running.',
  },
  PROVIDER_BAD_MODEL: {
    title: 'The configured model is unavailable',
    hint: 'The model name may be wrong or not enabled for this account. Pick another model in Settings → AI.',
  },
  OUTPUT_PARSE: {
    title: "The model's reply could not be used",
    hint: 'Retry — if it keeps happening, this model may not follow the required output format. Try a different one.',
  },
  TASK_TIMEOUT: {
    title: 'The task timed out',
    hint: 'Retry usually works. A smaller file or a faster model helps if it keeps timing out.',
  },
  INTERNAL: {
    title: 'Something went wrong on our side',
    hint: 'Retry — if it persists, the details are in Task Center.',
  },
};

export interface ResolvedTaskError {
  /** Short, user-facing headline. Never empty. */
  title: string;
  /** What the user can do about it. Absent when we only have raw text. */
  hint?: string;
  /** The structured code, when the backend supplied a known one. */
  code?: AiErrorCode;
}

/** i18next's `t` narrowed to what we use: key + English default. */
export type TranslateFn = (key: string, defaultValue: string) => string;

/** Raw engine text is unbounded (stack fragments, serialized JSON bodies);
 *  a toast that long is unreadable, so the fallback is clipped. */
const RAW_MAX = 160;

const IDENTITY_T: TranslateFn = (_key, defaultValue) => defaultValue;

function isKnownCode(value: unknown): value is AiErrorCode {
  return typeof value === 'string' && (AI_ERROR_CODES as readonly string[]).includes(value);
}

function truncate(s: string, max: number): string {
  const trimmed = s.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max - 1)}…` : trimmed;
}

/**
 * Resolve a failed task into copy worth showing.
 *
 * @param metadata  `task_tracking.metadata` — the backend puts `error_code` here.
 * @param rawError  `task_tracking.error_msg`, used when there is no known code.
 * @param t         i18next `t`; omit to get the English defaults (tests, non-React callers).
 */
export function resolveTaskError(
  metadata: Record<string, unknown> | null | undefined,
  rawError?: string | null,
  t: TranslateFn = IDENTITY_T,
): ResolvedTaskError {
  const code = metadata?.error_code;

  if (isKnownCode(code)) {
    const fallback = DEFAULTS[code];
    return {
      code,
      title: t(`errors.${code}.title`, fallback.title),
      hint: t(`errors.${code}.hint`, fallback.hint),
    };
  }

  // No code (older row, unclassified failure, or a code this build doesn't
  // know): show the raw error rather than a generic message, so nothing the
  // engine reported is hidden from the user.
  const raw = (rawError || '').trim();
  if (raw) return { title: truncate(raw, RAW_MAX) };

  return { title: t('errors.UNKNOWN.title', 'The task failed') };
}

/** One-line form for toasts, which take a single string. */
export function formatTaskError(
  metadata: Record<string, unknown> | null | undefined,
  rawError?: string | null,
  t: TranslateFn = IDENTITY_T,
): string {
  const { title, hint } = resolveTaskError(metadata, rawError, t);
  return hint ? `${title} — ${hint}` : title;
}
