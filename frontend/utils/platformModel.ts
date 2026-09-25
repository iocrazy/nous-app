// frontend/utils/platformModel.ts
//
// How a platform (Nous) catalog row is NAMED and whether it is OFFERED on the
// user side. One module so every surface — the Providers card, the task
// pickers, the agent editor — reads both rules the same way.
//
// Naming (2026-09-24, user request): the admin AI Models card labels each row
// by its `actual_model` (e.g. `doubao-embedding-vision-251215`) and falls back
// to the row `name` when `actual_model` is empty (local-daemon rows such as
// jimeng-local have no model selector). The user side used to print
// `display_name` (`Doubao Embedding (Vision)`), so nobody could match a user's
// list against the admin page by eye. The primary label now follows the admin
// rule exactly; `display_name` rides along as secondary text when it says
// something the primary does not.
//
// Only the visible text changes. The picker VALUE stays the row `name`
// (`nous:<name>` in task assignments) — that is what routing and saved
// settings key on, and it is stable while `actual_model` is admin-editable.
//
// Availability: a row whose last probe FAILED is not offered. `ok`,
// `not_probed` and never-probed (null / absent) rows are — "not checked" is not
// a verdict in either direction. This reverses #1838's "mark, don't hide" at
// the user's request; the cost is that a probe false negative hides a working
// model until the next hourly probe clears it.
//
// Pure and i18n-free on purpose, like utils/modelHealth: any component can
// import it cheaply.

import type { NousModelPublic } from '../types/api';

type LabelSource = Pick<NousModelPublic, 'name' | 'display_name' | 'actual_model'>;

export interface PlatformModelLabel {
  /** Same string the admin AI Models card shows for this row. */
  primary: string;
  /** `display_name` when it differs from `primary`, else null. */
  secondary: string | null;
}

export function platformModelLabel(m: LabelSource): PlatformModelLabel {
  const actual = m.actual_model?.trim();
  const primary = actual || m.name;
  const display = m.display_name?.trim();
  const secondary = display && display !== primary ? display : null;
  return { primary, secondary };
}

/**
 * One-line form for places that can only hold plain text (native <option>).
 * The separator is ` · ` so it cannot be confused with the ` — ` that
 * introduces a warning suffix in the pickers.
 */
export function platformModelText(m: LabelSource): string {
  const { primary, secondary } = platformModelLabel(m);
  return secondary ? `${primary} · ${secondary}` : primary;
}

/** False only for a row whose last probe failed. */
export function isPlatformModelAvailable(
  m: Partial<Pick<NousModelPublic, 'last_test_status'>>,
): boolean {
  return m.last_test_status !== 'fail';
}
