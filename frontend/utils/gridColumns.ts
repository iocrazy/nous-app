/**
 * Pure helper: compute the number of grid columns for a given CONTAINER width.
 *
 * Size-stable by construction: the column count is derived from a card-width
 * band, never from viewport breakpoints. Narrowing the container (e.g. the
 * resource info panel opening) therefore DROPS COLUMNS while cards keep
 * roughly the same width — it never re-flows into fewer, giant cards.
 *
 * History — the bug this replaces: the previous rule short-circuited on
 * `isMobile`, which useGridVirtualizer derived from the CONTAINER width
 * (`width < 768`), not the viewport. Opening the 320px info panel on a 1340px
 * layout took the grid container to ~780px, tripped that branch, and collapsed
 * a 7-column grid of 181px cards into 2 columns of 384px cards — cards
 * doubling in size the moment you clicked one. A phone is already served by
 * the band rule (a 375px container yields 2 columns), so the branch bought
 * nothing and cost size stability.
 */

/** Cards are never narrower than this when a wider layout is possible. */
export const MIN_CARD_WIDTH = 160
/** Hard cap — beyond this a column is added instead of growing the card.
 *  Mirrors the `.downloads-grid` CSS band `minmax(160px, 220px)`. */
export const MAX_CARD_WIDTH = 220
/** Inter-card gap, matching the grid markup's `gap: 12px`. */
export const GRID_GAP = 12

/** Sanity bound so a pathological width can't spin the widening loop. */
const MAX_COLUMNS = 24

/** Width one card gets when `width` is split into `columns` gapped columns. */
export function cardWidthFor(width: number, columns: number): number {
  const safe = Math.max(1, columns)
  return (width - GRID_GAP * (safe - 1)) / safe
}

export function columnsForWidth(width: number): number {
  // Safe default before the first ResizeObserver measurement.
  if (!Number.isFinite(width) || width <= 0) return 2

  // How many MIN_CARD_WIDTH cards fit, gaps included.
  let columns = Math.max(1, Math.floor((width + GRID_GAP) / (MIN_CARD_WIDTH + GRID_GAP)))

  // Widening pass: on a container too narrow for even one min-width card a
  // single column would stretch one card across the whole width. Add columns
  // until each card is at or under the cap.
  while (columns < MAX_COLUMNS && cardWidthFor(width, columns) > MAX_CARD_WIDTH) {
    columns += 1
  }

  return columns
}
