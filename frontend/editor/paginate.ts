/**
 * Paged-mode break computation (laper-style Paged view).
 *
 * Pure function over measured row boxes: walk the rows in document order,
 * accumulate a page from the first row's top, and whenever a row's BOTTOM
 * would overflow the page height, emit a break BEFORE that row (the dashed
 * separator + page number renders in the inter-row gap) and start the next
 * page at that row's top. A row taller than a whole page still advances the
 * page window (one break before it) rather than looping.
 *
 * This is a VISUAL pagination — blocks are never split mid-row, matching the
 * laper reference (a dashed rule with a centred page number between blocks).
 * True print pagination (dialogue MORE / CONT'D) is out of scope here.
 */

/** ~55 Courier lines per screenplay page at 13.5px × 1.7 line-height. */
export const PAGE_CONTENT_PX = 1260;

export interface RowBox {
  top: number;
  bottom: number;
}

export interface PageBreak {
  /** y offset (same coordinate space as the row boxes) to draw the rule at. */
  y: number;
  /** The page that ENDS at this break (1-based). */
  page: number;
}

export function computePageBreaks(rows: RowBox[], pageHeight: number = PAGE_CONTENT_PX): PageBreak[] {
  if (rows.length === 0) return [];
  const breaks: PageBreak[] = [];
  let pageStart = rows[0].top;
  let page = 1;
  for (let i = 1; i < rows.length; i += 1) {
    const row = rows[i];
    if (row.bottom - pageStart > pageHeight) {
      breaks.push({ y: row.top - 9, page });
      page += 1;
      pageStart = row.top;
    }
  }
  return breaks;
}
