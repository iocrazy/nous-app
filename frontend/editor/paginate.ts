/**
 * Paged-mode layout (Phase B v2 — real page seams).
 *
 * Pure function over measured rows: walk the document's rows (scene headings,
 * element rows, windowed-scene placeholders) in order, fill fixed-height pages,
 * and emit a SEAM before the first row that no longer fits. A seam carries the
 * FILLER needed to pad the current page to its fixed height, so the renderer
 * can draw a bottom page edge, a gap, and the next page's top edge + number.
 *
 * Keep-together rules (screenplay conventions):
 *  - a CHARACTER cue is never orphaned at a page bottom — if the break lands
 *    right after one, it moves before the cue;
 *  - a SCENE HEADING is never the last row of a page — the break moves before
 *    the heading (i.e. the whole scene starts on the next page).
 *  Rules cascade (heading + cue stacked both move) but never move the break
 *  above the current page's start (a pathological all-keep page still breaks).
 *  A row only binds to its OWN scene's content: when the next row starts a new
 *  scene (heading / placeholder — e.g. runs of EMPTY scenes, which measure as
 *  consecutive headings), the keep rule does not apply, so the cascade cannot
 *  retreat across whole scenes and blank out the page with filler.
 *
 * Blocks are never split mid-row: a row taller than a whole page overflows its
 * page (accepted; true intra-dialogue splitting with MORE / CONT'D would need
 * the editing model to split one element across two DOM nodes — out of scope).
 *
 * Coordinates are CONTENT coordinates: measured with any already-rendered
 * seams subtracted out, so the computation is a one-pass fixed point (seam
 * insertion never changes content coordinates → breaks stay stable).
 */

/** ~55 Courier lines per screenplay page at 13.5px × 1.7 line-height. */
export const PAGE_CONTENT_PX = 1260;

/** Non-filler height of a seam — the laper-style dashed rule row
 *  (`.mh-page-seam-rule`, 40px). Kept in sync with editorShellStyles.ts.
 *  The EditorShell measurement subtracts each seam's real getBoundingClientRect
 *  height, so this constant documents that rendered height rather than driving
 *  the layout math. */
export const SEAM_CHROME_PX = 40;

export type RowKind =
  | 'heading'
  | 'action'
  | 'character'
  | 'dialogue'
  | 'paren'
  | 'transition'
  | 'comment'
  | 'subtitle'
  | 'placeholder';

export interface MeasuredRow {
  /** Seam anchor: an element id, or `scene:<id>` for a heading/placeholder. */
  key: string;
  kind: RowKind;
  top: number;
  bottom: number;
}

export interface PageSeamSpec {
  /** Render the seam BEFORE the row with this key. */
  beforeKey: string;
  /** Pad the CURRENT page by this much so its content box hits page height. */
  filler: number;
  /** The page that ENDS at this seam (1-based); the next page is page + 1. */
  page: number;
}

export interface PageLayout {
  seams: PageSeamSpec[];
  pageCount: number;
}

/** Rows that must not be the last row on a page (they bind to the next row). */
function keepsWithNext(kind: RowKind): boolean {
  return kind === 'heading' || kind === 'character';
}

/** Rows that BEGIN a scene: another scene's heading, or a windowed stand-in. */
function startsScene(kind: RowKind): boolean {
  return kind === 'heading' || kind === 'placeholder';
}

/**
 * Whether `prev` truly introduces `next` (keep-with-next applies). A heading
 * or cue only binds to its OWN scene's content: when the next row starts a
 * NEW scene, `prev` is the complete tail of an (empty) scene, and binding it
 * would chain the cascade across every consecutive empty scene — retreating
 * the break to the page start and leaving a near-full page of filler.
 */
function bindsToNext(prev: MeasuredRow, next: MeasuredRow): boolean {
  return keepsWithNext(prev.kind) && !startsScene(next.kind);
}

export function computePageLayout(
  rows: MeasuredRow[],
  pageHeight: number = PAGE_CONTENT_PX,
): PageLayout {
  if (rows.length === 0) return { seams: [], pageCount: 1 };
  const seams: PageSeamSpec[] = [];
  let pageStartIndex = 0;
  let page = 1;
  let i = 1;
  while (i < rows.length) {
    const pageStartTop = rows[pageStartIndex].top;
    if (rows[i].bottom - pageStartTop > pageHeight) {
      // Break before row i — then walk the keep-with-next chain upward so a
      // heading / character cue moves along with the row it introduces.
      let b = i;
      while (b - 1 > pageStartIndex && bindsToNext(rows[b - 1], rows[b])) b -= 1;
      const filler = Math.max(0, pageHeight - (rows[b].top - pageStartTop));
      seams.push({ beforeKey: rows[b].key, filler, page });
      page += 1;
      pageStartIndex = b;
      i = b + 1;
    } else {
      i += 1;
    }
  }
  return { seams, pageCount: page };
}
