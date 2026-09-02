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
