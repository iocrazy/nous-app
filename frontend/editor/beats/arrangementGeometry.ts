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

/**
 * Minimum on-screen card width. The laper-style card carries a title + a
 * two-line summary + a footer (duration chip / Edit Beat), so a short beat still
 * needs room to be legible and grabbable — hence a generous floor, not 40px.
 */
export const MIN_CARD_PX = 180;

/** Visual gap subtracted from each card's rendered width so neighbours breathe. */
export const CARD_GAP_PX = 8;

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
 * Total timeline length in seconds: the greater of the per-script target
 * runtime (M3) and the furthest beat end, floored at 60s, rounded UP to a whole
 * minute so the ruler always ends on a clean tick, and clamped to
 * MAX_TIMELINE_SEC. `targetSec` is optional (null / non-positive is ignored) so
 * the historic no-arg call keeps its exact behaviour.
 */
export function computeTotalSec(beats: Placement[], targetSec?: number | null): number {
  const furthest = beats.reduce((max, b) => {
    if (b.start_sec == null) return max;
    const end = b.start_sec + (b.duration_sec ?? 0);
    return end > max ? end : max;
  }, 0);
  const target = targetSec != null && Number.isFinite(targetSec) && targetSec > 0 ? targetSec : 0;
  const floored = Math.max(target, furthest, 60);
  return Math.min(MAX_TIMELINE_SEC, Math.ceil(floored / 60) * 60);
}

/** A beat placement carrying its id, for conform (proportional stretch). */
export interface ConformInput {
  id: string;
  start_sec: number | null;
  duration_sec: number | null;
}

/** A conformed placement — always arranged (start_sec is a concrete second). */
export interface ConformResult {
  id: string;
  start_sec: number;
  duration_sec: number | null;
}

/**
 * Proportionally rescale every ARRANGED beat when the target total changes
 * (`oldTotal` → `newTotal`), snapping the result to the arrangement grid. Used
 * by the "Stretch beats proportionally" conform option: each start/duration is
 * multiplied by `newTotal / oldTotal`, snapped to `granularity`, and a present
 * duration is floored at one grid step so a beat never collapses to zero.
 * Unarranged beats (null start) are left out — there is nothing on the timeline
 * to stretch. A non-positive `oldTotal` yields no changes (guard against /0).
 */
export function conformBeats(
  beats: ConformInput[],
  oldTotal: number,
  newTotal: number,
  granularity: number,
): ConformResult[] {
  if (oldTotal <= 0) return [];
  const scale = newTotal / oldTotal;
  const out: ConformResult[] = [];
  for (const b of beats) {
    if (b.start_sec == null) continue;
    const start = snapSec(b.start_sec * scale, granularity);
    const duration =
      b.duration_sec == null
        ? null
        : Math.max(granularity, snapSec(b.duration_sec * scale, granularity));
    out.push({ id: b.id, start_sec: start, duration_sec: duration });
  }
  return out;
}

/** Seconds ruler under 3 minutes, minutes ruler at/above. */
export function chooseTickUnit(totalSec: number): TickUnit {
  return totalSec < SECONDS_UNIT_CEILING ? 'seconds' : 'minutes';
}

/** Drag snap grid — FINE, per user feedback ("只能 5 秒 5 秒的移动"): 1s in
 * seconds mode, 5s in minutes mode. NLE-style precision; the guide chip shows
 * the exact landing value while dragging. */
export function snapGranularity(unit: TickUnit): number {
  return unit === 'seconds' ? 1 : 5;
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

/**
 * Scroll-left that keeps the timeline point currently under `anchorClientX`
 * pinned under the same screen x after a zoom change (cursor-anchored zoom, à la
 * dnd-timeline). `viewportLeft` is the viewport's left edge in client coords;
 * `prevScrollLeft` its current horizontal scroll. Clamped to ≥0.
 */
export function anchorScrollLeft(args: {
  prevPxPerSec: number;
  nextPxPerSec: number;
  anchorClientX: number;
  viewportLeft: number;
  prevScrollLeft: number;
}): number {
  const { prevPxPerSec, nextPxPerSec, anchorClientX, viewportLeft, prevScrollLeft } = args;
  const offsetX = anchorClientX - viewportLeft;
  const timeAtCursor = pxToTime(prevScrollLeft + offsetX, prevPxPerSec);
  const nextContentX = timeToPx(timeAtCursor, nextPxPerSec);
  return Math.max(0, nextContentX - offsetX);
}

export interface Tick {
  sec: number;
  label: string;
  major: boolean;
}

/**
 * laper-style ruler label. Minutes use the film apostrophe (`0'`, `5'`, `10'`);
 * the seconds ruler shows sub-minute ticks as `Ns` and minute boundaries as the
 * apostrophe form (`0s`, `30s`, `1'`).
 */
function labelTick(sec: number, unit: TickUnit): string {
  if (unit === 'minutes') return `${Math.round(sec / 60)}'`;
  if (sec === 0) return '0s';
  return sec % 60 === 0 ? `${sec / 60}'` : `${sec}s`;
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
      label: major ? labelTick(rounded, unit) : '',
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

/** A card's resolved timeline placement: pixel x, packed lane, full width. */
export interface CardLayout {
  x: number;
  lane: number;
  width: number;
}

/**
 * laper-style sequential layout. Lanes split ONLY on true time overlap
 * (B starts before A ends); back-to-back or merely min-width-crowded beats stay
 * on one row, each card starting at its own time but PUSHED right past the
 * previous card when the zoom leaves no room (approximate positioning — the
 * ruler stays true, the flow stays single-row). Zero-length beats at the same
 * instant don't "overlap", so they sit adjacent on one lane instead of
 * stacking. Renderers subtract their visual gap from `width`; the push itself
 * packs cards edge-to-edge so the rendered gap equals that subtraction.
 */
export function layoutCards(beats: LaneInput[], pxPerSec: number): Map<string, CardLayout> {
  // Stable tiebreak on id. `id` is TYPED as string but arrives from the API as a
  // JSON number (Snowflake bigint), so coerce before comparing — a bare
  // `a.id.localeCompare` throws "localeCompare is not a function" the moment two
  // beats share a start_sec (M2 only ever exercised a single-beat sort).
  const ordered = [...beats].sort(
    (a, b) => a.start_sec - b.start_sec || String(a.id).localeCompare(String(b.id)),
  );
  const laneTimeEnds: number[] = [];
  const lanePxEnds: number[] = [];
  const result = new Map<string, CardLayout>();
  for (const beat of ordered) {
    const timeEnd = beat.start_sec + (beat.duration_sec ?? 0);
    let lane = laneTimeEnds.findIndex((end) => end <= beat.start_sec);
    if (lane === -1) {
      lane = laneTimeEnds.length;
      laneTimeEnds.push(timeEnd);
      lanePxEnds.push(0);
      const x = timeToPx(beat.start_sec, pxPerSec);
      const width = cardWidthPx(beat.duration_sec, pxPerSec);
      lanePxEnds[lane] = x + width;
      result.set(beat.id, { x, lane, width });
      continue;
    }
    const width = cardWidthPx(beat.duration_sec, pxPerSec);
    const x = Math.max(timeToPx(beat.start_sec, pxPerSec), lanePxEnds[lane]);
    laneTimeEnds[lane] = timeEnd;
    lanePxEnds[lane] = x + width;
    result.set(beat.id, { x, lane, width });
  }
  return result;
}

/**
 * Canvas width: the ruler's full span or the furthest pushed card edge,
 * whichever is wider — otherwise a card pushed past the timeline end clips.
 */
export function layoutExtentPx(
  layout: Map<string, CardLayout>,
  totalSec: number,
  pxPerSec: number,
): number {
  let max = timeToPx(totalSec, pxPerSec);
  for (const l of layout.values()) if (l.x + l.width > max) max = l.x + l.width;
  return max;
}

