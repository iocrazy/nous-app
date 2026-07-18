/**
 * Per-script display-zoom persistence — mirrors paginationStorage/formatStorage
 * (client-side localStorage, no backend jsonb clobber risk).
 *
 * Zoom is a PURELY VISUAL scale of the sheet (CSS `zoom` on `.mh-sheet`): it
 * reflows the paper so scroll size stays correct, but changes NOTHING about the
 * stored document, the export/print semantics, or the pagination break points.
 * The pagination engine measures in logical (100%) coordinates regardless of
 * zoom, so seams land on the exact same rows at every zoom level.
 *
 * Stored as a BARE number string ('115'), not JSON — an illegal or out-of-range
 * value falls back to null so the caller applies DEFAULT_ZOOM.
 */

/** Discrete zoom tiers the +/- buttons step between (percent of print size). */
export const ZOOM_STEPS = [75, 90, 100, 115, 125, 150, 175, 200] as const;

/** Default visual size — 115% aligns the on-screen size to laper.ai's larger
 *  default while 100% stays the print baseline (13.5px Courier). */
export const DEFAULT_ZOOM = 115;
export const MIN_ZOOM = 75;
export const MAX_ZOOM = 200;

const storageKey = (scriptId: string): string => `editor.zoom.${scriptId}`;

/** Nearest legal tier to an arbitrary percent (keeps +/- stepping on-grid even
 *  if a hand-edited localStorage value lands between tiers). */
export function nearestZoomStep(zoom: number): number {
  return ZOOM_STEPS.reduce((best, step) =>
    Math.abs(step - zoom) < Math.abs(best - zoom) ? step : best,
  );
}

/** One tier up/down from `zoom`, clamped to the tier range. */
export function stepZoom(zoom: number, direction: 1 | -1): number {
  const current = nearestZoomStep(zoom);
  const index = ZOOM_STEPS.indexOf(current as (typeof ZOOM_STEPS)[number]);
  const next = index + direction;
  if (next < 0 || next >= ZOOM_STEPS.length) return current;
  return ZOOM_STEPS[next];
}

export function readStoredZoom(scriptId: string): number | null {
  try {
    const raw = localStorage.getItem(storageKey(scriptId));
    if (raw == null) return null;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < MIN_ZOOM || value > MAX_ZOOM) return null;
    return value;
  } catch {
    return null;
  }
}

export function persistZoom(scriptId: string, zoom: number): void {
  try {
    localStorage.setItem(storageKey(scriptId), String(zoom));
  } catch (err) {
    // Non-fatal: a writer in private mode just loses the per-script memory.
    console.error('[editor] failed to persist zoom', err);
  }
}
