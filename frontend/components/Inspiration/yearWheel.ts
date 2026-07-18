// frontend/components/Inspiration/yearWheel.ts
// Pure logic for the Activity calendar year wheel (see the approved mockup
// docs/superpowers/specs/2026-07-17-activity-year-wheel-mockup.html). Kept free
// of React so the caps / ordering / aggregation can be unit-tested exhaustively.

// Number of fixed slots in the wheel (selected year centered, 2 above / 2 below).
export const WHEEL_SLOTS = 5;
const HALF = (WHEEL_SLOTS - 1) / 2;

/**
 * The years shown in the wheel, top→bottom ascending (older above, newer below),
 * centered on `center`. Any slot that would land later than `nowYear` is `null`
 * (future cap: those years are never rendered). Length is always WHEEL_SLOTS.
 */
export function visibleYearSlots(center: number, nowYear: number): (number | null)[] {
  const slots: (number | null)[] = [];
  for (let off = -HALF; off <= HALF; off++) {
    const y = center + off;
    slots.push(y <= nowYear ? y : null);
  }
  return slots;
}

/**
 * Move the centered year by one step. `dir` is +1 to move toward now, -1 into
 * the past. The result is capped at `nowYear` (never the future); there is no
 * lower bound. Returns the (possibly unchanged) new center.
 */
export function stepYear(center: number, dir: number, nowYear: number): number {
  return Math.min(nowYear, center + dir);
}

/**
 * Month indices (0-11) that carry at least one note, from activity rows shaped
 * `{ day: 'YYYY-MM-DD', cnt }`. The month is parsed from the string (not via
 * `Date`) to stay timezone-independent.
 */
export function monthsWithNotes(rows: { day: string; cnt: number }[]): Set<number> {
  const set = new Set<number>();
  for (const r of rows) {
    if (r.cnt > 0 && typeof r.day === 'string' && r.day.length >= 7) {
      const m = Number(r.day.slice(5, 7)) - 1;
      if (m >= 0 && m <= 11) set.add(m);
    }
  }
  return set;
}
