/**
 * Pure helper: compute the number of grid columns for a given container width.
 *
 * Rules:
 *  - isMobile → 2 (always)
 *  - width <= 0 → 2 (safe default before first ResizeObserver measurement)
 *  - else: Math.min(8, Math.max(2, Math.floor(width / 172)))
 */
export function columnsForWidth(width: number, isMobile: boolean): number {
  if (isMobile) return 2
  if (width <= 0) return 2
  return Math.min(8, Math.max(2, Math.floor(width / 172)))
}
