/**
 * Memo-pin geometry (Beats M4) — pure, DOM-free math for the timeline memo rail.
 *
 * A memo pin is an inspiration-library note anchored to a whole-second offset on
 * a script's Beats arrangement timeline. This module owns the three "where/what
 * does this pin look like" decisions that are painful to verify through the DOM,
 * so MemoRail stays a thin render + pointer shell (mirroring how
 * arrangementGeometry backs ArrangementView):
 *
 *   - pinColorFor — which beat's colour a pin at `sec` inherits;
 *   - layoutMemoPins — greedy row packing so horizontally-near cards don't
 *     overlap (laper's staggered memo stacks);
 *   - memoPinPath — the relaxed SVG curve from a ruler dot down to its card.
 *
 * All exports are referentially transparent — no time, randomness, or globals.
 */

/** A beat's colouring inputs (the only fields pinColorFor needs). */
export interface PinColorBeat {
  start_sec: number | null;
  duration_sec: number | null;
  color: string | null;
}

/**
 * The colour a pin at `sec` inherits: the colour of the beat whose
 * `[start_sec, start_sec + duration_sec)` half-open interval contains `sec`.
 * Beats with no start, no positive duration, or no colour never colour a pin.
 * When several coloured beats overlap `sec`, the EARLIEST-starting one wins
 * (deterministic; matches the bottom-most timeline lane). Returns null for a pin
 * that sits in a gap or only over colourless beats → the caller renders neutral
 * grey.
 */
export function pinColorFor(sec: number, beats: PinColorBeat[]): string | null {
  let best: { start: number; color: string } | null = null;
  for (const b of beats) {
    if (b.start_sec == null || b.duration_sec == null || b.duration_sec <= 0) continue;
    if (!b.color) continue;
    const start = b.start_sec;
    const end = start + b.duration_sec;
    if (sec < start || sec >= end) continue;
    if (best === null || start < best.start) best = { start, color: b.color };
  }
  return best ? best.color : null;
}

/** A memo card's horizontal footprint (px) for overlap packing. */
export interface MemoPinBox {
  id: string;
  /** Left edge of the card in canvas px. */
  x: number;
  /** Card width in px. */
  width: number;
}

/**
 * Greedy row packing so memo cards whose horizontal spans collide are pushed to
 * a lower row instead of overlapping. Pins are placed left-to-right (stable
 * tiebreak on id — ids arrive as Snowflake strings); each lands in the first row
 * whose last card ends (with `gapPx` breathing room) at or before this card's
 * left edge, else a fresh row below. Returns id → row index (0 = top row).
 */
export function layoutMemoPins(pins: MemoPinBox[], gapPx: number): Map<string, number> {
  const ordered = [...pins].sort(
    (a, b) => a.x - b.x || String(a.id).localeCompare(String(b.id)),
  );
  const rowEnds: number[] = [];
  const out = new Map<string, number>();
  for (const pin of ordered) {
    let row = rowEnds.findIndex((end) => end <= pin.x);
    if (row === -1) {
      row = rowEnds.length;
      rowEnds.push(0);
    }
    rowEnds[row] = pin.x + pin.width + gapPx;
    out.set(pin.id, row);
  }
  return out;
}

/**
 * A relaxed connector from a ruler dot at (`pinX`, `pinY`) down to a card top
 * connector at (`cardX`, `cardY`). A cubic Bézier with both control points on
 * the vertical mid-line gives a vertical tangent at each end — the pin drops
 * straight, swings toward the (possibly offset) card, and arrives straight
 * (laper's slack curve). Returns an SVG path `d` string.
 */
export function memoPinPath(
  pinX: number,
  pinY: number,
  cardX: number,
  cardY: number,
): string {
  const midY = pinY + (cardY - pinY) / 2;
  return `M ${pinX} ${pinY} C ${pinX} ${midY} ${cardX} ${midY} ${cardX} ${cardY}`;
}

/**
 * Film-apostrophe offset label for a memo pin / anchor chip: `45s` under a
 * minute, `1'` on the minute, `1'30` past it. Shared by the timeline pin chip
 * and the inspiration-library reverse anchor chip so both read identically.
 */
export function formatAnchorSec(sec: number): string {
  const whole = Math.max(0, Math.round(sec));
  if (whole < 60) return `${whole}s`;
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  return s === 0 ? `${m}'` : `${m}'${String(s).padStart(2, '0')}`;
}
