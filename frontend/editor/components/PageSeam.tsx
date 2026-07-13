/**
 * PageSeam — the in-flow page boundary for Paged mode v2 (real page look).
 *
 * Rendered BEFORE the first row of a new page. Structure, top to bottom:
 *   1. filler (paddingTop) — pads the CURRENT page's content box out to the
 *      fixed page height, so every page bottom lands on the same line;
 *   2. a paper bottom edge (hairline + soft shadow);
 *   3. the inter-page gap (painted with the chrome surface so the continuous
 *      sheet reads as two separate sheets);
 *   4. the next paper's top edge, carrying the screenplay-style page number
 *      (top-right, with the trailing period: "2.").
 *
 * The band bleeds past the sheet's text padding (negative margins) so the
 * edges run edge-to-edge across the paper. Heights 12+28+24 must stay in sync
 * with SEAM_CHROME_PX in paginate.ts.
 */
export function PageSeam({ page, filler }: { page: number; filler: number }) {
  return (
    <div
      className="mh-page-seam"
      style={{ paddingTop: filler }}
      aria-hidden="true"
      contentEditable={false}
      data-testid="page-seam"
    >
      <div className="mh-page-seam-bottom" />
      <div className="mh-page-seam-gap" />
      <div className="mh-page-seam-topedge">
        <span className="mh-page-seam-num">{page + 1}.</span>
      </div>
    </div>
  );
}
