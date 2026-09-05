// frontend/components/resources/generated/generatedFilters.ts
//
// URL search-params ↔ Generated-inbox filter state. Pure: no React, no clock
// of its own — `sinceIsoFor` takes `now` so a date preset is testable without
// freezing time globally.
//
// The URL is the single source of truth for the inbox's view state (tab +
// filters), so a link a writer pastes to a colleague reproduces exactly what
// they were looking at. Param names mirror the wire (`origin_kind`,
// `project_id`, `media_kind`) so a URL reads the same as the request it
// produces.

import type { GeneratedFilterState, GeneratedListOptions } from '../../../services/generatedService';

// ─── Vocabularies ───────────────────────────────────────────────────────────

/** The tabs. `deleted` is a real `review_state` but never a tab — a row in it
 *  is not something the inbox lists, so it is not accepted from the URL. */
export const FILTER_STATES = ['unreviewed', 'saved', 'in_assets', 'all'] as const;

/**
 * Origin kinds the backend stamps on a generation, in the order the Source
 * chip lists them. Labels are i18n keys rather than literals: this module is
 * pure and must not depend on a `t` function to be usable (or testable).
 */
export const SOURCE_OPTIONS = [
  { kind: 'canvas_run', labelKey: 'generated.source.canvas_run', fallback: 'Canvas' },
  { kind: 'canvas_upload', labelKey: 'generated.source.canvas_upload', fallback: 'Canvas Upload' },
  { kind: 'shot_generate', labelKey: 'generated.source.shot_generate', fallback: 'Storyboard Image' },
  { kind: 'shot_video', labelKey: 'generated.source.shot_video', fallback: 'Storyboard Video' },
  { kind: 'agent_run', labelKey: 'generated.source.agent_run', fallback: 'Agent' },
  { kind: 'chat_upload', labelKey: 'generated.source.chat_upload', fallback: 'Chat Upload' },
] as const;

const SOURCE_KINDS: readonly string[] = SOURCE_OPTIONS.map((o) => o.kind);

/**
 * The Source chip's last row, and NOT an origin kind.
 *
 * `canvas_upload` is three unrelated things on the wire — a file a person
 * dropped, a mask/brush composite an editor baked, and a library asset
 * transcoded so the image-to-image bridge could fetch it. Only the first is
 * something to triage, so the other two are hidden and this option asks for
 * them back. It therefore toggles a BOOLEAN, not a membership in
 * `originKinds`: adding it to that list would send the backend an
 * origin_kind it has never written and quietly match nothing.
 */
export const INTERMEDIATE_OPTION = {
  labelKey: 'generated.source.intermediate',
  fallback: 'Intermediate Inputs',
} as const;

/**
 * The Type chip. `media_kind` is open-ended on the wire, but these four are
 * the only values anything WRITES today, so these four get a filter button:
 *
 *   image / video  canvas + storyboard generations, chat uploads
 *   file           a chat upload that is neither (`chat_upload.py`'s
 *                  `media_kind_for_mime` folds every other mime here)
 *   audio          a My Uploads audio file saved through "As Asset" — the
 *                  P6 mint path (`generated_inbox_service.py`'s
 *                  `ACCEPTED_ASSET_FILE_KINDS`) writes it
 *
 * Anything outside the list is still dropped when it arrives in the URL: a
 * `media_kind` the backend has never written would ask the router for a
 * filter that quietly matches nothing.
 */
export const MEDIA_KINDS = ['image', 'video', 'audio', 'file'] as const;
export type MediaKind = (typeof MEDIA_KINDS)[number];

/** Relative windows offered by the Date chip, resolved to an instant at call
 *  time — storing an absolute ISO string in the URL would silently mean
 *  something different tomorrow than it did when the link was copied. */
export const SINCE_PRESETS = ['24h', '7d', '30d'] as const;
export type SincePreset = (typeof SINCE_PRESETS)[number];

const PRESET_MS: Record<SincePreset, number> = {
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
};

// ─── State ──────────────────────────────────────────────────────────────────

export interface GeneratedFilters {
  state: GeneratedFilterState;
  /** Multi-select. Empty means "every source", which is what the router
   *  assumes when the param is absent. */
  originKinds: string[];
  /** Single-select; only canvas-origin rows carry a project. */
  projectId: string | null;
  mediaKind: MediaKind | null;
  model: string | null;
  since: SincePreset | null;
  /** Show the masks / brush bakes / transcoded references the inbox hides. */
  includeIntermediate: boolean;
}

export function defaultGeneratedFilters(): GeneratedFilters {
  return {
    state: 'unreviewed',
    originKinds: [],
    projectId: null,
    mediaKind: null,
    model: null,
    since: null,
    includeIntermediate: false,
  };
}

// ─── Parse / serialize ──────────────────────────────────────────────────────

/** A param that is present but empty carries no information — treat it as
 *  absent rather than as a filter matching the empty string. */
function nonEmpty(value: string | null): string | null {
  const trimmed = value?.trim() ?? '';
  return trimmed === '' ? null : trimmed;
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  const candidate = nonEmpty(value);
  return candidate !== null && (allowed as readonly string[]).includes(candidate)
    ? (candidate as T)
    : null;
}

/**
 * Read filter state out of the URL. Unknown values are DROPPED, never
 * forwarded: the query string is user-editable, and passing `state=deleted`
 * or an invented origin kind straight through would ask the router for a
 * filter that quietly matches nothing.
 */
export function parseFilters(searchParams: URLSearchParams): GeneratedFilters {
  return {
    state: oneOf(searchParams.get('state'), FILTER_STATES) ?? 'unreviewed',
    originKinds: searchParams
      .getAll('origin_kind')
      .map((k) => k.trim())
      .filter((k) => SOURCE_KINDS.includes(k)),
    projectId: nonEmpty(searchParams.get('project_id')),
    mediaKind: oneOf(searchParams.get('media_kind'), MEDIA_KINDS),
    model: nonEmpty(searchParams.get('model')),
    since: oneOf(searchParams.get('since'), SINCE_PRESETS),
    // Only the literal the serializer writes turns this on. Accepting any
    // truthy string would make `?include_intermediate=0` mean "yes".
    includeIntermediate: searchParams.get('include_intermediate') === 'true',
  };
}

/**
 * The inverse. Defaults are omitted so the landing URL stays bare — but
 * `state=all` IS written, because "all" is a choice, not the default.
 */
export function serializeFilters(filters: GeneratedFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (filters.state !== 'unreviewed') sp.set('state', filters.state);
  for (const kind of filters.originKinds) sp.append('origin_kind', kind);
  if (filters.projectId) sp.set('project_id', filters.projectId);
  if (filters.mediaKind) sp.set('media_kind', filters.mediaKind);
  if (filters.model) sp.set('model', filters.model);
  if (filters.since) sp.set('since', filters.since);
  if (filters.includeIntermediate) sp.set('include_intermediate', 'true');
  return sp;
}

// ─── Derived ────────────────────────────────────────────────────────────────

/** Resolve a Date preset against a caller-supplied instant. */
export function sinceIsoFor(preset: SincePreset | null, now: Date): string | undefined {
  if (preset === null) return undefined;
  return new Date(now.getTime() - PRESET_MS[preset]).toISOString();
}

/**
 * Filter state → `fetchGenerated` options. Absent filters are left off the
 * object entirely (not sent as `null`), so "not filtered" and "filtered to
 * the router's own default" produce the same request.
 */
export function listOptionsFor(filters: GeneratedFilters, now: Date): GeneratedListOptions {
  const opts: GeneratedListOptions = { state: filters.state };
  if (filters.originKinds.length > 0) opts.originKinds = [...filters.originKinds];
  if (filters.projectId) opts.projectId = filters.projectId;
  if (filters.mediaKind) opts.mediaKind = filters.mediaKind;
  if (filters.model) opts.model = filters.model;
  const since = sinceIsoFor(filters.since, now);
  if (since) opts.since = since;
  if (filters.includeIntermediate) opts.includeIntermediate = true;
  return opts;
}

/** Immutable add/remove for the multi-select Source chip. */
export function toggleOriginKind(current: readonly string[], kind: string): string[] {
  return current.includes(kind) ? current.filter((k) => k !== kind) : [...current, kind];
}

/** True when anything beyond the tab is narrowing the list — drives the
 *  "no results because you filtered" empty state. */
export function hasActiveFilters(filters: GeneratedFilters): boolean {
  return (
    filters.originKinds.length > 0 ||
    filters.projectId !== null ||
    filters.mediaKind !== null ||
    filters.model !== null ||
    filters.since !== null ||
    // Widening counts as active too: the "no results because you filtered"
    // empty state is the wrong thing to show, but a chip that looks untouched
    // while the page is showing masks is worse.
    filters.includeIntermediate
  );
}
