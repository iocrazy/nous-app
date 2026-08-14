// frontend/utils/modelHealth.ts
//
// Shared read of a platform model's self-check result (spec
// 2026-08-14-model-health-surfacing-design §F2).
//
// The backend has probed every enabled platform model on a schedule for
// months, but the result never left the admin surface — the public model list
// simply didn't carry the columns. So a user could assign an agent a model
// whose probe had been failing all day, and the first sign of trouble was a
// chat that failed silently. These helpers are the one place "unhealthy"
// is defined, so the agent editor and AI Settings can't drift apart on it.
//
// Deliberately NOT here: any notion of blocking. A red light is advisory —
// the probe has produced at least one false negative in production
// (2026-08-14: a model marked unreachable that worked end-to-end), so it
// warns and the user decides.
//
// Also deliberately NOT here: the wording of the warning, or the relative-time
// formatting. What must not drift between surfaces is the READING of the
// signal; the phrasing legitimately differs (AI Settings is hardcoded English
// and formats ages with utils/taskDisplay, the agent editor is translated and
// uses utils/relativeTime). Keeping this module free of those imports also
// keeps it free of the i18n instance — importing it must stay cheap enough
// that any component can, which is not true of utils/relativeTime.

import type { NousModelPublic, NousModelType } from '../types';

export type ModelHealthStatus = 'ok' | 'fail';

export interface ModelHealth {
  status: ModelHealthStatus;
  /** ISO-8601 timestamp of the probe, or null when the backend didn't send one. */
  testedAt: string | null;
  /**
   * Why the last probe failed, as a closed enum (backend migration 427), or
   * null when the row predates the column. Never free text: the backend
   * derives it from the exception type and HTTP status alone, which is what
   * makes it safe to show on a list every user can read.
   */
  code: string | null;
}

/**
 * Model types the backend probe can actually judge.
 *
 * The probe branches on type and sends everything that is not `asr` or
 * `embedding` to `POST {base_url}/chat/completions`
 * (backend/app/services/ai/mediahub_model_health.py). So `image` / `video` /
 * `tts` are ALWAYS recorded as failing, whatever their real state: a
 * text-to-image model 404s on a chat endpoint, and CLI-backed models have no
 * base_url at all so the probe builds an invalid URL. Production confirms it —
 * 3 of the 4 `fail` rows on 2026-08-14 were exactly this, all three models fine.
 *
 * Surfacing those would put three permanently-lit warnings in front of every
 * user, and the first thing they'd learn is to ignore the badge — which is the
 * problem this feature exists to fix, not a smaller version of it. So the
 * unjudgeable types stay silent, on the same rule as never-probed models: no
 * signal is better than a false one.
 *
 * The real fix belongs in the probe (dispatch per type, or write no status for
 * types it cannot reach); tracked in the spec's backlog. Widen this set only
 * once the probe actually speaks that type's protocol.
 */
const PROBED_TYPES: ReadonlySet<NousModelType> = new Set<NousModelType>([
  'llm',
  'embedding',
  'asr',
]);

/**
 * Health by model NAME (the id the pickers key on), for every model that has
 * actually been probed AND whose type the probe can judge. A model with no
 * status is left OUT rather than defaulted: "never checked" is not a health
 * claim in either direction.
 */
export function buildModelHealth(
  models: NousModelPublic[],
): Record<string, ModelHealth> {
  const out: Record<string, ModelHealth> = {};
  for (const m of models) {
    if (!PROBED_TYPES.has(m.type)) continue;
    if (m.last_test_status !== 'ok' && m.last_test_status !== 'fail') continue;
    out[m.name] = {
      status: m.last_test_status,
      testedAt: m.last_tested_at ?? null,
      code: m.last_test_code ?? null,
    };
  }
  return out;
}

/**
 * The failure codes this build can put words to. A fixed table, not a template:
 * a key is only ever returned for a value that is IN this list, so no string
 * arriving from the backend can be turned into rendered text by accident.
 */
const REASON_CODES: ReadonlySet<string> = new Set([
  'timeout',
  'unreachable',
  'auth',
  'rate_limit',
  'model_not_found',
  'upstream_error',
  'bad_response',
  'other',
]);

/**
 * i18n key for a failure code, or `null` when there is nothing specific to say.
 *
 * `null` covers two different situations that want the same treatment: the row
 * was probed before the column existed (no code at all), and the backend enum
 * grew ahead of this deploy (a code we have no translation for). Both fall back
 * to the caller's plain "health check failed" wording — rendering an untranslated
 * key would be worse than saying less.
 *
 * The wording itself deliberately lives in the locale files rather than here,
 * for the same reason this module holds no other copy: it must stay importable
 * without pulling in the i18n instance (see the module-boundaries test).
 */
export function healthReasonKey(code: string | null | undefined): string | null {
  if (!code || !REASON_CODES.has(code)) return null;
  return `aiSettings.healthReason.${code}`;
}
