/**
 * Arrangement timeline geometry (Beats M2) — pure, DOM-free math.
 *
 * Every "where does this land on the timeline" decision lives here so the
 * ArrangementView component stays a thin render + pointer shell. Kept pure and
 * separately unit-tested (editor/__tests__/arrangementGeometry.test.ts): the
 * card position/width, drag snapping, ruler ticks, lane packing and zoom-fit are
 * exactly the things that are painful to verify through the DOM.
 *
 * Units: `sec` is a whole-second offset on the timeline; `pxPerSec` is the zoom
 * factor (screen pixels per timeline second). All exports are referentially
 * transparent — no time, no randomness, no globals.
 */

/** Minimum on-screen card width so a zero/short-duration beat stays grabbable. */
export const MIN_CARD_PX = 40;

/** Zoom band (px per timeline second). */
export const MIN_PX_PER_SEC = 0.02;
export const MAX_PX_PER_SEC = 40;
/** Initial zoom before a Fit view / stored value applies (10s ≈ 60px). */
export const DEFAULT_PX_PER_SEC = 6;

/** Ruler tick unit — seconds for short-form, minutes for features. */
export type TickUnit = 'seconds' | 'minutes';

/** A beat's timeline placement (the only fields geometry needs). */
export interface Placement {
  start_sec: number | null;
  duration_sec: number | null;
}

const SECONDS_UNIT_CEILING = 180; // <3min reads in seconds

/**
 * Display ceiling for the ruler (4h). The server accepts start_sec up to PG
 * INTEGER max, so without a clamp one corrupt row (start_sec≈2^31) renders a
 * ~1.2M-tick ruler and a multi-gigapixel canvas — a tab-freezing DoS on open.
 * Cards beyond the ceiling keep their data; the ruler just stops here.
 */
export const MAX_TIMELINE_SEC = 4 * 60 * 60;

/**
 * Total timeline length in seconds: the furthest beat end, floored at 60s,
 * rounded UP to a whole minute so the ruler always ends on a clean tick, and
 * clamped to MAX_TIMELINE_SEC.
 */
export function computeTotalSec(beats: Placement[]): number {
  const furthest = beats.reduce((max, b) => {
    if (b.start_sec == null) return max;
    const end = b.start_sec + (b.duration_sec ?? 0);
    return end > max ? end : max;
  }, 0);
  const floored = Math.max(furthest, 60);
  return Math.min(MAX_TIMELINE_SEC, Math.ceil(floored / 60) * 60);
}

/** Seconds ruler under 3 minutes, minutes ruler at/above. */
export function chooseTickUnit(totalSec: number): TickUnit {
  return totalSec < SECONDS_UNIT_CEILING ? 'seconds' : 'minutes';
}

/** Drag snap grid: 5s in seconds mode, 30s in minutes mode. */
export function snapGranularity(unit: TickUnit): number {
  return unit === 'seconds' ? 5 : 30;
}

/** Snap a (possibly negative) offset to the nearest grid step, clamped to 0. */
export function snapSec(sec: number, granularity: number): number {
  if (granularity <= 0) return Math.max(0, Math.round(sec));
  return Math.max(0, Math.round(sec / granularity) * granularity);
}

export function timeToPx(sec: number, pxPerSec: number): number {
  return sec * pxPerSec;
}

export function pxToTime(px: number, pxPerSec: number): number {
  return pxPerSec === 0 ? 0 : px / pxPerSec;
}

/** Card width in px from its duration, never below the min-width floor. */
export function cardWidthPx(durationSec: number | null, pxPerSec: number): number {
  if (durationSec == null || durationSec <= 0) return MIN_CARD_PX;
  return Math.max(MIN_CARD_PX, durationSec * pxPerSec);
}

export function clampPxPerSec(pxPerSec: number): number {
  if (!Number.isFinite(pxPerSec)) return DEFAULT_PX_PER_SEC;
  return Math.min(MAX_PX_PER_SEC, Math.max(MIN_PX_PER_SEC, pxPerSec));
}

/** Zoom that fits the whole timeline into `viewportPx` of screen width. */
export function fitPxPerSec(totalSec: number, viewportPx: number): number {
  if (totalSec <= 0 || viewportPx <= 0) return MIN_PX_PER_SEC;
  return clampPxPerSec(viewportPx / totalSec);
}

const ZOOM_FACTOR = 1.25;

/** One zoom step in (dir=1) or out (dir=-1), clamped to the band. */
export function stepPxPerSec(pxPerSec: number, dir: 1 | -1): number {
  return clampPxPerSec(dir === 1 ? pxPerSec * ZOOM_FACTOR : pxPerSec / ZOOM_FACTOR);
}

export interface Tick {
  sec: number;
  label: string;
  major: boolean;
}

function labelSeconds(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s === 0 ? `${m}m` : `${m}:${String(s).padStart(2, '0')}`;
}

/** Smallest minute interval that keeps the major-tick count reasonable (≤12). */
function majorMinuteInterval(totalSec: number): number {
  const totalMin = totalSec / 60;
  for (const step of [1, 2, 5, 10, 15, 30, 60]) {
    if (totalMin / step <= 12) return step;
  }
  return 60;
}

/** Hard cap on emitted ticks — belt-and-braces after the totalSec clamp. */
const MAX_TICKS = 2000;

/**
 * Ruler ticks from 0..totalSec inclusive. Majors carry a label; the minor ticks
 * between them (half a major step) are unlabelled hairlines.
 */
export function buildTicks(totalSec: number, unit: TickUnit): Tick[] {
  const majorSec = unit === 'seconds' ? 10 : majorMinuteInterval(totalSec) * 60;
  const minorSec = Math.max(1, majorSec / 2);
  const ticks: Tick[] = [];
  for (let sec = 0; sec <= totalSec + 0.5 && ticks.length < MAX_TICKS; sec += minorSec) {
    const rounded = Math.round(sec);
    const major = rounded % majorSec === 0;
    ticks.push({
      sec: rounded,
      label: major ? (unit === 'seconds' ? labelSeconds(rounded) : `${rounded / 60}m`) : '',
      major,
    });
  }
  return ticks;
}

export interface LaneInput {
  id: string;
  start_sec: number;
  duration_sec: number | null;
}

/**
 * Greedy lane packing: process beats left-to-right, drop each into the first
 * lane whose last card ends at/before this card starts, else open a new lane.
 * `minDurationSec` is the timeline-time equivalent of the min card width, so
 * two tiny/zero-length cards at the same instant land on separate lanes instead
 * of visually stacking.
 */
export function assignLanes(beats: LaneInput[], minDurationSec: number): Map<string, number> {
  const ordered = [...beats].sort(
    (a, b) => a.start_sec - b.start_sec || a.id.localeCompare(b.id),
  );
  const laneEnds: number[] = [];
  const result = new Map<string, number>();
  for (const beat of ordered) {
    const width = Math.max(beat.duration_sec ?? 0, minDurationSec);
    const end = beat.start_sec + width;
    let lane = laneEnds.findIndex((laneEnd) => laneEnd <= beat.start_sec);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(end);
    } else {
      laneEnds[lane] = end;
    }
    result.set(beat.id, lane);
  }
  return result;
}
