/**
 * Justified (Eagle-style) layout as a PURE PRE-PASS, so the view can
 * virtualize: rows are computed from aspect ratios alone — greedy packing at
 * a target height, scaling each row down to fit the container exactly once
 * it would overflow (the overflowing item stays in the row, Flickr-style).
 * The last row never stretches.
 *
 * Because every row's height is exact before anything renders, the
 * virtualizer needs no DOM measurement — 100k items lay out in one O(n)
 * arithmetic pass (~ms), replacing the previous flex-wrap markup that
 * mounted ALL cards and died around 15k.
 *
 * ⚠️ THE PACKING IS SEQUENTIAL, SO A CHANGED RATIO RIPPLES FORWARD. Row
 * boundaries depend on every ratio before them, so when one item's aspect
 * ratio changes (a thumbnail measuring in and correcting its placeholder),
 * the row it lands in AND EVERY ROW AFTER IT can repartition — not just its
 * own row. This is inherent to justified layouts, not a defect here; Eagle and
 * Flickr behave the same way. The two mitigations that exist:
 *
 *  - the caller batches measurements per animation frame, so the tail
 *    repartitions once per frame rather than once per image;
 *  - `reuse` below keeps the untouched PREFIX verbatim, so the work (and the
 *    identity churn) is bounded to the tail rather than the whole list.
 *
 * What neither removes: items after the change can visibly shift. The only
 * real cures are knowing dimensions before first paint (capture them on
 * upload) or measuring ahead of the viewport.
 */

export interface JustifiedRow {
  /** Index range [start, end) into the items array. */
  start: number;
  end: number;
  /** Exact row height in px; item i renders at width ar[i] * height. */
  height: number;
}

export interface JustifiedOptions {
  targetRowHeight?: number;
  gap?: number;
}

/** Display clamp — mirrors aspectRatioOf's sane range (ResourceGrid). */
export function clampAspectRatio(ar: number): number {
  if (!Number.isFinite(ar) || ar <= 0) return 1;
  return Math.min(3, Math.max(0.4, ar));
}

/**
 * Reuse hint for a repeat layout of the SAME list at the SAME width/options,
 * where only aspect ratios at or after `firstChangedIndex` have changed.
 *
 * The greedy pass reads the ratio array strictly left to right, so every row
 * that ends at or before `firstChangedIndex` is provably identical to last
 * time and can be kept verbatim. Rows from that point on are recomputed.
 *
 * This is a COST optimisation, not a stability one: the recomputed tail can
 * still repartition. See `computeJustifiedRows` for what that means on screen.
 */
export interface JustifiedReuse {
  prevRows: JustifiedRow[];
  firstChangedIndex: number;
}

export function computeJustifiedRows(
  aspectRatios: number[],
  containerWidth: number,
  { targetRowHeight = 170, gap = 8 }: JustifiedOptions = {},
  reuse?: JustifiedReuse,
): JustifiedRow[] {
  if (containerWidth <= 0 || aspectRatios.length === 0) return [];

  const rows: JustifiedRow[] = [];
  let start = 0;
  let sumAr = 0;

  // Keep every previously-computed row that ends at or before the first
  // changed item; those rows saw none of the changed ratios.
  if (reuse && reuse.prevRows.length > 0 && reuse.firstChangedIndex > 0) {
    for (const row of reuse.prevRows) {
      if (row.end > reuse.firstChangedIndex) break;
      rows.push(row);
      start = row.end;
    }
  }

  for (let i = start; i < aspectRatios.length; i += 1) {
    const ar = clampAspectRatio(aspectRatios[i]);
    const n = i - start + 1;
    const widthAtTarget = (sumAr + ar) * targetRowHeight + gap * (n - 1);

    if (widthAtTarget >= containerWidth) {
      // Close the row INCLUDING item i, scaled to fill the width exactly.
      const height = (containerWidth - gap * (n - 1)) / (sumAr + ar);
      rows.push({ start, end: i + 1, height });
      start = i + 1;
      sumAr = 0;
    } else {
      sumAr += ar;
    }
  }

  if (start < aspectRatios.length) {
    // Last row under-fills: keep the target height, never stretch.
    rows.push({ start, end: aspectRatios.length, height: targetRowHeight });
  }

  return rows;
}

/**
 * Per-item widths for one row, with a floor.
 *
 * A justified row gives every item the same height, so width is purely
 * `ar * height` — and a 9:16 video is 2.4x narrower than a 4:3 image. In the
 * downloads grid each card carries a four-column stats strip under the
 * thumbnail (likes / comments / shares / collects), and below roughly 174px
 * those four numbers stop fitting: `142.7K` at 9px bold needs ~33px, the card
 * spends 16px on padding and 18px on gaps, so a cell only clears it from
 * `(W - 34) / 4 >= 35`. Narrower than that and the strip renders as squeezed
 * blocks with the digits clipped — which is what the user reported
 * (2026-09-15), on exactly the portrait douyin videos.
 *
 * Raising the row height instead does not work: to widen a 0.5625 item to
 * 190px the row has to be ~340px tall, which blows every image in that row up
 * to 450px. The height is shared; the widths are not. So the floor is applied
 * HERE, by taking the surplus from the wide items:
 *
 *   - items under `minWidth` are pinned to `minWidth`
 *   - the rest shrink proportionally to absorb exactly what was taken
 *   - the row's TOTAL width is unchanged, so justification is exact and the
 *     deliberately-short last row stays short
 *
 * The cost is landing on the wide items: they get cropped a little more by the
 * thumbnail's `object-cover`. That is the right trade — a slightly tighter
 * crop is invisible, unreadable numbers are not.
 *
 * Returns natural widths untouched when nothing is under the floor, and falls
 * back to an equal split of the same total when the floor cannot be paid for
 * out of this row (every item narrow, or the wide ones would drop below it).
 */
export function distributeRowWidths(
  aspectRatios: number[],
  rowHeight: number,
  { minWidth = 0 }: { minWidth?: number } = {},
): number[] {
  const n = aspectRatios.length;
  if (n === 0) return [];

  const natural = aspectRatios.map((ar) => clampAspectRatio(ar) * rowHeight);
  if (minWidth <= 0) return natural;
  if (!natural.some((w) => w < minWidth)) return natural;

  // The row's own total is the budget — NOT the container width. For a full
  // row the two are identical (that is how computeJustifiedRows derives the
  // height), but the LAST row deliberately under-fills, and funding the floor
  // from the container there would stretch the wide items to fill a row that
  // was never meant to be full.
  const total = natural.reduce((a, w) => a + w, 0);
  const equalSplit = () => natural.map(() => total / n);

  // Pin iteratively. Shrinking the wide items to pay for a pinned one can push
  // a MIDDLING item under the floor in turn — so whoever falls below gets
  // pinned as well and the scale is recomputed. Without this the fix just
  // moves the unreadable card somewhere else (observed: a 1:1 card dropping to
  // 172px while funding a 9:16 one).
  const pinned = natural.map((w) => w < minWidth);
  for (;;) {
    const pinnedCount = pinned.reduce((a, isPinned) => a + (isPinned ? 1 : 0), 0);
    if (pinnedCount === n) return equalSplit();

    const surplus = natural.reduce((a, w, i) => a + (pinned[i] ? 0 : w), 0);
    const remaining = total - minWidth * pinnedCount;
    if (surplus <= 0 || remaining <= 0) return equalSplit();

    const scale = remaining / surplus;
    const fell = natural.map((w, i) => !pinned[i] && w * scale < minWidth);
    if (!fell.some(Boolean)) {
      return natural.map((w, i) => (pinned[i] ? minWidth : w * scale));
    }
    for (let i = 0; i < n; i += 1) if (fell[i]) pinned[i] = true;
  }
}
