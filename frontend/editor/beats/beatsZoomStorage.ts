/**
 * Per-script Arrangement zoom persistence (M2) — a BARE number string
 * (px-per-second), mirroring zoomStorage's storage discipline (no JSON, no
 * backend jsonb clobber). Zoom is a purely visual scale of the timeline and
 * changes nothing about stored beat data.
 *
 * An illegal / out-of-band value falls back to null so the caller applies a
 * Fit view (or the default zoom) instead.
 */
import { MAX_PX_PER_SEC, MIN_PX_PER_SEC } from './arrangementGeometry';

const storageKey = (scriptId: string): string => `editor.beatsZoom.${scriptId}`;

export function readStoredBeatsZoom(scriptId: string): number | null {
  try {
    const raw = localStorage.getItem(storageKey(scriptId));
    if (raw == null) return null;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < MIN_PX_PER_SEC || value > MAX_PX_PER_SEC) {
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

export function persistBeatsZoom(scriptId: string, pxPerSec: number): void {
  try {
    localStorage.setItem(storageKey(scriptId), String(pxPerSec));
  } catch (err) {
    // Non-fatal: a writer in private mode just loses the per-script memory.
    console.error('[editor] failed to persist beats zoom', err);
  }
}
