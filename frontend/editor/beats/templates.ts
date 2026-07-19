/**
 * Beats M3 — methodology template registry + instantiation (pure, DOM-free).
 *
 * A template stores its beats as TIMELINE PERCENTAGES (0–100), independent of
 * runtime, so the same save-the-cat sheet resolves against a 60-second short or
 * a 110-minute feature. `instantiateTemplate` maps those anchors to concrete
 * `BeatInput` rows for a target total, snapping to the arrangement grid so the
 * generated beats sit exactly where a drag would settle them. Adding a new
 * methodology is a registry entry + its i18n block — nothing else changes.
 *
 * Percentage anchors come straight from issue #1471:
 *  - a POINT anchor (pctStart == pctEnd) is an instant marker (e.g. Midpoint);
 *    it gets a minimum visible length = max(1% of total, snap granularity).
 *  - an INTERVAL anchor spans its window (e.g. Fun & Games 27–50%).
 *
 * i18n keys follow `editor.beatTpl.<key>.name` / `.desc` for the template and
 * `editor.beatTpl.<key>.<role>.name` / `.guide` for each beat. `beat_role` is
 * persisted as `<key>.<role>` (the free-form-beat convention is a null role).
 */
import type { Beat, BeatInput } from '../sceneService';
import { BEAT_COLORS } from './beatColors';
import { snapSec } from './arrangementGeometry';

export interface TemplateBeat {
  /** Role key within the template (namespaced to `<template>.<role>` on save). */
  role: string;
  /** Timeline start, as a percentage of the total (0–100). */
  pctStart: number;
  /** Timeline end, as a percentage of the total; == pctStart for a point beat. */
  pctEnd: number;
}

export interface BeatTemplate {
  /** Stable registry key (also the beat_role namespace). */
  key: string;
  /** i18n key for the template's display name. */
  nameKey: string;
  /** i18n key for the one-line "what is this method" blurb. */
  descKey: string;
  beats: TemplateBeat[];
}

/** Save the Cat! — Blake Snyder's 15-beat sheet (110-min feature baseline). */
const SAVE_THE_CAT: BeatTemplate = {
  key: 'save_the_cat',
  nameKey: 'editor.beatTpl.save_the_cat.name',
  descKey: 'editor.beatTpl.save_the_cat.desc',
  beats: [
    { role: 'opening_image', pctStart: 0, pctEnd: 1 },
    { role: 'theme_stated', pctStart: 4.5, pctEnd: 4.5 },
    { role: 'setup', pctStart: 1, pctEnd: 9 },
    { role: 'catalyst', pctStart: 11, pctEnd: 11 },
    { role: 'debate', pctStart: 11, pctEnd: 23 },
    { role: 'break_into_two', pctStart: 23, pctEnd: 23 },
    { role: 'b_story', pctStart: 27, pctEnd: 27 },
    { role: 'fun_and_games', pctStart: 27, pctEnd: 50 },
    { role: 'midpoint', pctStart: 50, pctEnd: 50 },
    { role: 'bad_guys_close_in', pctStart: 50, pctEnd: 68 },
    { role: 'all_is_lost', pctStart: 68, pctEnd: 68 },
    { role: 'dark_night_of_the_soul', pctStart: 68, pctEnd: 77 },
    { role: 'break_into_three', pctStart: 77, pctEnd: 77 },
    { role: 'finale', pctStart: 77, pctEnd: 100 },
    { role: 'final_image', pctStart: 100, pctEnd: 100 },
  ],
};

/** Core Five Beats — the compact dramatic spine. */
const FIVE_BEATS: BeatTemplate = {
  key: 'five_beats',
  nameKey: 'editor.beatTpl.five_beats.name',
  descKey: 'editor.beatTpl.five_beats.desc',
  beats: [
    { role: 'inciting_incident', pctStart: 11, pctEnd: 11 },
    { role: 'progressive_complications', pctStart: 11, pctEnd: 68 },
    { role: 'crisis', pctStart: 68, pctEnd: 77 },
    { role: 'climax', pctStart: 77, pctEnd: 95 },
    { role: 'resolution', pctStart: 95, pctEnd: 100 },
  ],
};

/** Kishōtenketsu (起承転結) — the four-act structure without central conflict. */
const KISHOTENKETSU: BeatTemplate = {
  key: 'kishotenketsu',
  nameKey: 'editor.beatTpl.kishotenketsu.name',
  descKey: 'editor.beatTpl.kishotenketsu.desc',
  beats: [
    { role: 'ki', pctStart: 0, pctEnd: 25 },
    { role: 'sho', pctStart: 25, pctEnd: 50 },
    { role: 'ten', pctStart: 50, pctEnd: 75 },
    { role: 'ketsu', pctStart: 75, pctEnd: 100 },
  ],
};

export const BEAT_TEMPLATES: readonly BeatTemplate[] = [
  SAVE_THE_CAT,
  FIVE_BEATS,
  KISHOTENKETSU,
] as const;

/** Look a template up by key (undefined for an unknown key). */
export function getTemplate(key: string): BeatTemplate | undefined {
  return BEAT_TEMPLATES.find((t) => t.key === key);
}

/** A point anchor still needs a visible, grabbable length on the timeline. */
function pointLenFor(totalSec: number, granularity: number): number {
  return Math.max(
    granularity,
    snapSec(Math.max(Math.round(totalSec * 0.01), granularity), granularity),
  );
}

/**
 * Map a single percentage anchor to a grid-aligned `{ start_sec, duration_sec }`
 * for `totalSec`. A POINT anchor (`pctEnd <= pctStart`) gets `pointLen`; an
 * INTERVAL anchor spans its percentage window (min one snap granularity). Shared
 * by the built-in template path and the user-custom path so both snap identically.
 */
function placeAnchor(
  pctStart: number,
  pctEnd: number,
  totalSec: number,
  granularity: number,
  pointLen: number,
): { start_sec: number; duration_sec: number } {
  const start = snapSec((pctStart / 100) * totalSec, granularity);
  const isPoint = pctEnd <= pctStart;
  const duration = isPoint
    ? pointLen
    : Math.max(granularity, snapSec(((pctEnd - pctStart) / 100) * totalSec, granularity));
  return { start_sec: start, duration_sec: duration };
}

/**
 * Instantiate a template's percentage anchors against `totalSec` into concrete
 * `BeatInput` rows. `translate` maps the beat name / guidance i18n keys to text
 * (identity in unit tests — the geometry is translation-independent). Colors
 * cycle the M1 Morandi palette so a generated sheet reads as a coherent band.
 */
export function instantiateTemplate(
  template: BeatTemplate,
  totalSec: number,
  granularity: number,
  translate: (key: string) => string,
): BeatInput[] {
  const pointLen = pointLenFor(totalSec, granularity);
  return template.beats.map((b, i) => {
    const { start_sec, duration_sec } = placeAnchor(
      b.pctStart,
      b.pctEnd,
      totalSec,
      granularity,
      pointLen,
    );
    return {
      title: translate(`editor.beatTpl.${template.key}.${b.role}.name`),
      summary: translate(`editor.beatTpl.${template.key}.${b.role}.guide`),
      start_sec,
      duration_sec,
      beat_role: `${template.key}.${b.role}`,
      color: BEAT_COLORS[i % BEAT_COLORS.length],
    };
  });
}

// ── User custom templates (M3.5) ─────────────────────────────────────────────
//
// A user template stores its own beat titles (not i18n role keys) alongside the
// percentage anchors: the sheet a user arranged by hand is theirs verbatim. It
// persists as a `beat_templates` row (anchors JSONB). The wizard renders these
// beside the built-in three; instantiation carries title/summary/color straight
// through and leaves `beat_role` null (custom beats are free-form).

export interface CustomTemplateAnchor {
  /** The beat's own title (user text) — used verbatim on instantiate. */
  title: string;
  /** Optional beat guidance/summary; carried through when present. */
  summary?: string | null;
  /** Timeline start, as a percentage of the total (0–100, 1 decimal). */
  pctStart: number;
  /** Timeline end, as a percentage of the total; == pctStart for a point beat. */
  pctEnd: number;
  /** Card color-strip hex; carried through when present. */
  color?: string | null;
}

/** Round to one decimal place (the persisted anchor precision). */
function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

/** Clamp a percentage into the valid [0, 100] band. */
function clampPct(n: number): number {
  return Math.min(100, Math.max(0, n));
}

/**
 * Reverse-compute percentage anchors from the beats a user has arranged on the
 * timeline, relative to `totalSec`. Unarranged beats (start_sec null — the tray)
 * are skipped; the rest are ordered by start. Each beat's title / summary / color
 * ride along so the saved template reproduces the exact sheet. A non-positive
 * total yields `[]` (percentages are undefined without a span).
 */
export function deriveTemplateAnchors(beats: Beat[], totalSec: number): CustomTemplateAnchor[] {
  if (!(totalSec > 0)) return [];
  return beats
    .filter((b) => b.start_sec != null)
    .slice()
    .sort((a, b) => (a.start_sec ?? 0) - (b.start_sec ?? 0))
    .map((b) => {
      const start = b.start_sec ?? 0;
      const dur = b.duration_sec ?? 0;
      return {
        title: b.title,
        summary: b.summary,
        pctStart: clampPct(round1((start / totalSec) * 100)),
        pctEnd: clampPct(round1(((start + dur) / totalSec) * 100)),
        color: b.color,
      };
    });
}

/**
 * Instantiate user-custom anchors against `totalSec` into `BeatInput` rows,
 * carrying the stored title / summary / color verbatim (no i18n) and snapping
 * to the arrangement grid exactly as the built-in path does. `beat_role` is null
 * — a custom beat has no namespaced methodology role.
 */
export function instantiateCustomAnchors(
  anchors: CustomTemplateAnchor[],
  totalSec: number,
  granularity: number,
): BeatInput[] {
  const pointLen = pointLenFor(totalSec, granularity);
  return anchors.map((a) => {
    const { start_sec, duration_sec } = placeAnchor(
      a.pctStart,
      a.pctEnd,
      totalSec,
      granularity,
      pointLen,
    );
    return {
      title: a.title,
      summary: a.summary ?? null,
      start_sec,
      duration_sec,
      beat_role: null,
      color: a.color ?? null,
    };
  });
}
