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

import type { NousModelPublic } from '../types';

export type ModelHealthStatus = 'ok' | 'fail';

export interface ModelHealth {
  status: ModelHealthStatus;
  /** ISO-8601 timestamp of the probe, or null when the backend didn't send one. */
  testedAt: string | null;
}

/**
 * Health by model NAME (the id the pickers key on), for every model that has
 * actually been probed. A model with no status is left OUT rather than
 * defaulted: "never checked" is not a health claim in either direction.
 */
export function buildModelHealth(
  models: NousModelPublic[],
): Record<string, ModelHealth> {
  const out: Record<string, ModelHealth> = {};
  for (const m of models) {
    if (m.last_test_status !== 'ok' && m.last_test_status !== 'fail') continue;
    out[m.name] = { status: m.last_test_status, testedAt: m.last_tested_at ?? null };
  }
  return out;
}

/** Names of models whose last probe failed. */
export function unhealthyModelNames(models: NousModelPublic[]): Set<string> {
  return new Set(models.filter((m) => m.last_test_status === 'fail').map((m) => m.name));
}
