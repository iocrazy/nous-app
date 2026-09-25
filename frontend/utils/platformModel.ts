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
// list against the admin page by eye. The label now follows the admin rule
// exactly and `display_name` is not shown at all (2026-09-25).
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
// A row that is `idle` (local nous-engine: authorized, not loaded) IS offered
// but not pickable — shown greyed with "Not loaded on nous-engine"; see
// platformModelAvailability for why.
//
// Pure and i18n-free on purpose, like utils/modelHealth: any component can
// import it cheaply.

import type { NousModelPublic } from '../types/api';

type LabelSource = Pick<NousModelPublic, 'name' | 'display_name' | 'actual_model'>;

export interface PlatformModelLabel {
  /** Same string the admin AI Models card shows for this row. */
  primary: string;
}

/**
 * The ONE name a platform row goes by on the user side: exactly the
 * identifier the admin entered (`actual_model`, else the row `name` for
 * local-daemon rows that have no model selector). `display_name` is NOT
 * shown anywhere on the user side (2026-09-25 user decision: "严格根据我
 * admin 端引入的模型名称，不要自己创造") — the display names on the
 * platform rows were authored by migrations, not by the admin, and a second
 * line that the admin page does not show is one more thing to match by eye.
 */
export function platformModelLabel(m: LabelSource): PlatformModelLabel {
  const actual = m.actual_model?.trim();
  return { primary: actual || m.name };
}

/**
 * Plain-text form for pickers and native <option>s. Same string as
 * `platformModelLabel().primary`; kept as its own function so a caller that
 * only needs text does not have to know about the label object.
 */
export function platformModelText(m: LabelSource): string {
  return platformModelLabel(m).primary;
}

/** False only for a row whose last probe failed. */
export function isPlatformModelAvailable(
  m: Partial<Pick<NousModelPublic, 'last_test_status'>>,
): boolean {
  return m.last_test_status !== 'fail';
}

export interface PlatformModelAvailability {
  selectable: boolean;
  /** Why a visible row cannot be picked. Pair with i18n `platformModel.notLoaded`. */
  reason?: 'not_loaded';
}

/**
 * Whether a row that IS offered (see isPlatformModelAvailable) can be picked.
 *
 * `idle` = a local nous-engine row that is authorized but not loaded (hourly
 * readiness read, migration 503). It stays visible but greyed out: on
 * 2026-09-24 a real chat to such a row got 503 "not loaded" back instead of
 * loading on demand, so offering it would hand the user a model that errors.
 * Visible rather than hidden so the user can see the model exists and why it
 * cannot be chosen right now.
 *
 * `fail` is not handled here on purpose — those rows are hidden before they
 * reach a picker (isPlatformModelAvailable), per the 2026-09-24 user decision.
 */
export function platformModelAvailability(
  m: Partial<Pick<NousModelPublic, 'last_test_status'>>,
): PlatformModelAvailability {
  if (m.last_test_status === 'idle') return { selectable: false, reason: 'not_loaded' };
  return { selectable: true };
}

export interface PlatformOptionAttrs {
  disabled: boolean;
  'data-availability'?: 'not_loaded';
  'data-description'?: string;
}

/**
 * Spread onto a model `<option>` inside UiSelect: an unselectable row becomes
 * `disabled` with `notLoadedText` (caller-localized) as its second line, and
 * UiSelect dims + titles it when it is the saved value. Native `<select>`s
 * cannot show a description — append the reason to the text there instead.
 */
export function platformOptionAttrs(
  m: Partial<Pick<NousModelPublic, 'last_test_status'>>,
  notLoadedText: string,
): PlatformOptionAttrs {
  const { selectable, reason } = platformModelAvailability(m);
  if (selectable) return { disabled: false };
  return { disabled: true, 'data-availability': reason, 'data-description': notLoadedText };
}
