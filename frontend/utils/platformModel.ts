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
// Availability (spec 2026-09-25): the server already drops rows whose probe
// failed and rows nous-engine no longer lists, so every row that reaches the
// user side IS offered. A row that is `idle` (local nous-engine: authorized,
// not loaded) is offered but not pickable — shown greyed with "Not loaded on
// nous-engine"; see platformModelAvailability for why.
//
// The list itself comes with the AI settings (`providers.nous` +
// `platform_models`); platformModelRows below is the one derivation every
// picker uses, so the Providers card, the task pickers, the agent editor and
// the canvas all show the same rows.
//
// Pure and i18n-free on purpose, like utils/modelHealth: any component can
// import it cheaply.

import type { AISettings } from '../types';
import type { PlatformModelEntry, PlatformModelStatus, PlatformModelType } from '../types/api';

type LabelSource = { name: string; actual_model?: string | null };

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

export interface PlatformModelAvailability {
  selectable: boolean;
  /** Why a visible row cannot be picked. Pair with i18n `platformModel.notLoaded`. */
  reason?: 'not_loaded';
}

/**
 * Whether a listed row can be picked, from its status (hooks/usePlatformStatus
 * overlays the live value on the one the settings carried).
 *
 * `idle` = a local nous-engine row that is authorized but not loaded. It stays
 * visible but greyed out: on 2026-09-24 a real chat to such a row got 503 "not
 * loaded" back instead of loading on demand, so offering it would hand the
 * user a model that errors. Visible rather than hidden so the user can see the
 * model exists and why it cannot be chosen right now. `not_probed` / unknown
 * is not a verdict in either direction and stays pickable.
 */
export function platformModelAvailability(
  status: PlatformModelStatus | null | undefined,
): PlatformModelAvailability {
  if (status === 'idle') return { selectable: false, reason: 'not_loaded' };
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
  status: PlatformModelStatus | null | undefined,
  notLoadedText: string,
): PlatformOptionAttrs {
  const { selectable, reason } = platformModelAvailability(status);
  if (selectable) return { disabled: false };
  return { disabled: true, 'data-availability': reason, 'data-description': notLoadedText };
}

/** One platform row as a picker sees it: the settings mapping plus its name. */
export interface PlatformModelRow extends PlatformModelEntry {
  name: string;
}

export interface PlatformModelRowsOptions {
  /** `enabled` (default): what pickers offer — the card's master switch on and
   *  the row not in `disabled_models`. `listed`: every row the user may toggle
   *  (the Providers card itself lists these, so it can switch rows back on). */
  scope?: 'enabled' | 'listed';
  /** Keep only these types; omit for all. */
  types?: readonly PlatformModelType[];
}

/**
 * The platform rows carried by the AI settings, in the server's order.
 *
 * Pure mapping, never a request: `providers.nous.models` / `enabled_models`
 * name the rows and `platform_models` maps each name to what a picker shows
 * (spec 2026-09-25 §3.1). `platform_models === null` is "the server could not
 * compute the view" — no rows, and nothing guessed. A name without a mapping
 * is skipped rather than shown as a bare id.
 */
export function platformModelRows(
  settings: Pick<AISettings, 'providers' | 'platform_models'> | null | undefined,
  { scope = 'enabled', types }: PlatformModelRowsOptions = {},
): PlatformModelRow[] {
  const mapping = settings?.platform_models;
  const nous = settings?.providers?.nous;
  if (!mapping || !nous) return [];
  if (scope === 'enabled' && nous.enabled === false) return [];
  const names = (scope === 'enabled' ? nous.enabled_models : nous.models) ?? [];
  const out: PlatformModelRow[] = [];
  for (const name of names) {
    const entry = mapping[name];
    if (!entry) continue;
    if (types && !types.includes(entry.type)) continue;
    out.push({ ...entry, name });
  }
  return out;
}
