/**
 * Elapsed-seconds helpers for the ClassicMode comfy node (Phase 5a B4).
 *
 * Split out from the view so the formatting is unit-testable without a
 * DOM, and the ticking hook is the only stateful piece.
 */

import { useEffect, useState } from 'react';

/** Format a whole-second count as `12s` or `1m 03s`. */
export function formatElapsed(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  if (safe < 60) return `${safe}s`;
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
}

/** Whole seconds between `startedAt` and `now` (clamped at 0). */
export function elapsedSeconds(startedAt: string | null, nowMs: number): number {
  if (!startedAt) return 0;
  const start = new Date(startedAt).getTime();
  if (Number.isNaN(start)) return 0;
  return Math.max(0, Math.floor((nowMs - start) / 1000));
}

/**
 * Live elapsed-seconds counter. Ticks once per second while `running` is
 * true and `startedAt` is set; the interval is cleared on unmount and
 * whenever the run stops (so a settled node stops counting).
 */
export function useElapsedSeconds(
  startedAt: string | null,
  running: boolean,
): number {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (!running || !startedAt) return;
    // Sync immediately so the first paint after start isn't a second stale.
    setNowMs(Date.now());
    const interval = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [running, startedAt]);

  return elapsedSeconds(startedAt, nowMs);
}
