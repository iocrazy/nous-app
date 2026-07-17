/**
 * PageSeam — the in-flow page boundary for Paged mode (laper-style seam).
 *
 * Rendered BEFORE the first row of a new page. Structure, top to bottom:
 *   1. filler (paddingTop) — pads the CURRENT page's content box out to the
 *      fixed page height, so every page bottom lands on the same line;
 *   2. a single dashed rule that runs the full paper width with the page
 *      number nested in its middle (dash-dash NUMBER dash-dash). The paper
 *      stays one continuous sheet (no bottom-edge / gap / top-edge break).
 *
 * The page number is the page that ENDS here (a bare integer, no trailing
 * period) — the seam after page 1's last row reads "1".
 *
 * The band bleeds past the sheet's text padding (negative margins) so the rule
 * runs edge-to-edge across the paper. The rule row's height must stay in sync
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
      <div className="mh-page-seam-rule">
        <span className="mh-page-seam-num">{page}</span>
      </div>
    </div>
  );
}
