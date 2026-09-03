// features/canvas-core/smart/aspectRatio.ts
//
// One requested ratio ('16:9') → one CSS `aspect-ratio` value ('16 / 9').
//
// Why a box has to exist BEFORE the pixels do (canvas fluency Task 7): an
// <img> with no width/height contributes zero height until its bytes decode,
// so a generation slot reflows the card — and shoves every node below it —
// at least twice per result. Reserving the box up front means the image
// lands INTO the space the shimmer cell was already holding.
//
// Deliberately dependency-free: it is read by `genSlots` (store path) and by
// the memo-wrapped output node view, and neither should pull a React module
// in through this helper.

/** Square. The honest answer when nothing better is knowable — NOT a claim
 *  that the result will be square. */
export const FALLBACK_ASPECT = '1 / 1';

/** 'auto' means "match whatever image feeds the prompt" (see autoRatio). The
 *  source is only measurable at dispatch, on the runner's side, so from the
 *  view's seat it is indistinguishable from "unknown". */
const AUTO = 'auto';

/**
 * CSS `aspect-ratio` for a requested ratio preset, or `null` when the ratio
 * is not knowable.
 *
 * Accepts the picker's `w:h` form and the CSS-native `w/h`. Everything else —
 * unset, `auto`, a free-text value, a zero or negative term — is `null`
 * rather than being passed through: an invalid `aspect-ratio` is DROPPED by
 * the browser, so emitting one would silently restore the collapsing box this
 * whole helper exists to prevent, while looking like it had done something.
 *
 * Callers pick what "not knowable" should mean for them. A cell that has a
 * shimmer sibling to agree with wants {@link cssAspectRatio}'s square; a lone
 * image that is free to size itself wants no box at all.
 */
export function cssAspectRatioOrNull(ratio: string | null | undefined): string | null {
  if (!ratio) return null;
  const raw = ratio.trim();
  if (!raw || raw.toLowerCase() === AUTO) return null;
  const parts = raw.split(/[:/]/);
  if (parts.length !== 2) return null;
  const [w, h] = parts.map((part) => Number(part.trim()));
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return null;
  return `${w} / ${h}`;
}

/**
 * Same, but square when nothing is knowable. For the grid cells, where a
 * placeholder and the image that replaces it MUST resolve to one box —
 * disagreeing there is the reflow the task exists to remove.
 */
export function cssAspectRatio(ratio: string | null | undefined): string {
  return cssAspectRatioOrNull(ratio) ?? FALLBACK_ASPECT;
}
