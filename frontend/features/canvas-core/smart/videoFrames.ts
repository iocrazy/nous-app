// features/canvas-core/smart/videoFrames.ts
//
// Frame-step math for the video lightbox (P2-8 — Infinite's videoFrameStep
// / seekPreviewFrames, adapted). We have no per-clip fps metadata, so we
// use a fixed 30fps step (Infinite's default clamp floor is the same).
// Pure functions — the <video> seeking / canvas export live in the
// component; this file stays jsdom-trivial to test.

/** Fixed 30fps frame step (no fps metadata on our durable streams). */
export const FRAME_STEP_SECONDS = 1 / 30;

/** Highest safe seek target — Infinite keeps half a frame off the end so
 *  the decoder never lands on a non-existent final boundary frame. */
function lastSafeTime(duration: number): number {
  if (!Number.isFinite(duration) || duration <= 0) return 0;
  return Math.max(0, duration - FRAME_STEP_SECONDS / 2);
}

/** Next currentTime after stepping `dir` (+1 / -1) frames, clamped inside
 *  the clip. Degenerate durations return the input time unchanged. */
export function nextFrameTime(
  currentTime: number,
  dir: 1 | -1,
  duration: number,
): number {
  if (!Number.isFinite(duration) || duration <= 0) return currentTime;
  const raw = currentTime + dir * FRAME_STEP_SECONDS;
  return Math.max(0, Math.min(lastSafeTime(duration), raw));
}

export type ExportWhich = 'first' | 'current' | 'last';

/** Seek target for an export-frame action. */
export function exportFrameTime(
  which: ExportWhich,
  currentTime: number,
  duration: number,
): number {
  if (which === 'first') return 0;
  if (which === 'last') return lastSafeTime(duration);
  return Math.max(0, Math.min(lastSafeTime(duration), currentTime));
}
