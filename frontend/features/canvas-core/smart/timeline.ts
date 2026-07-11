// features/canvas-core/smart/timeline.ts
//
// Timeline director node (G8-F1 — Infinite's LTX director, adapted):
// a multi-segment film described on one node. Pure segment CRUD helpers;
// the run wiring lives in timelineRun.ts, the chrome in TimelineNodeView.

export interface TimelineSegment {
  id: string;
  prompt: string;
  seconds: number;
}

export interface TimelineNodeData {
  segments?: TimelineSegment[];
  model?: string;
  aspect?: string;
  run_status?: 'idle' | 'queued' | 'running' | 'succeeded' | 'failed';
  run_error?: string | null;
  /** Live "segment i of N" readout (P2-1) — mirrored from the film task's
   *  metadata while polling, null when idle/settled. */
  run_progress?: { done: number; total: number } | null;
  /** 0-based index of the segment that broke a failed run (P2-1) —
   *  `segments_done` from the task metadata IS the failing index. */
  failed_index?: number | null;
  /** Per-segment tail-frame thumbnails (P2-1) — durable /cover URLs,
   *  index-aligned with segments (the last segment has no tail). */
  segment_thumbs?: string[];
}

export const DEFAULT_SEGMENT_SECONDS = 5;
export const MAX_TIMELINE_SEGMENTS = 12;
const MIN_SECONDS = 1;
const MAX_SECONDS = 10;

export function addSegment(segments: TimelineSegment[]): TimelineSegment[] {
  if (segments.length >= MAX_TIMELINE_SEGMENTS) return segments;
  return [
    ...segments,
    {
      id: `seg-${crypto.randomUUID()}`,
      prompt: '',
      seconds: DEFAULT_SEGMENT_SECONDS,
    },
  ];
}

/** A timeline always keeps at least one segment. */
export function removeSegment(
  segments: TimelineSegment[],
  id: string,
): TimelineSegment[] {
  if (segments.length <= 1) return segments;
  return segments.filter((s) => s.id !== id);
}

export function updateSegment(
  segments: TimelineSegment[],
  id: string,
  patch: Partial<Pick<TimelineSegment, 'prompt' | 'seconds'>>,
): TimelineSegment[] {
  return segments.map((s) => {
    if (s.id !== id) return s;
    const next = { ...s, ...patch };
    next.seconds = Math.min(MAX_SECONDS, Math.max(MIN_SECONDS, Math.round(next.seconds)));
    return next;
  });
}

export function totalSeconds(segments: TimelineSegment[]): number {
  return segments.reduce((sum, s) => sum + s.seconds, 0);
}

// ── Minimal-set upgrades (P2-1) ─────────────────────────────────────────────

/** Move a segment to a new position (drag reorder). Out-of-range targets
 *  clamp; invalid/same-index moves return the input untouched. */
export function reorderSegments(
  segments: TimelineSegment[],
  fromIndex: number,
  toIndex: number,
): TimelineSegment[] {
  if (fromIndex < 0 || fromIndex >= segments.length) return segments;
  const target = Math.max(0, Math.min(segments.length - 1, toIndex));
  if (target === fromIndex) return segments;
  const next = [...segments];
  const [moved] = next.splice(fromIndex, 1);
  next.splice(target, 0, moved);
  return next;
}

/** Edge-drag seconds: whole-second steps at the strip's px-per-second
 *  scale, clamped to the 1..10 envelope. Degenerate scales no-op. */
export function dragSeconds(
  startSeconds: number,
  deltaPx: number,
  pxPerSecond: number,
): number {
  if (!Number.isFinite(pxPerSecond) || pxPerSecond <= 0) return startSeconds;
  const raw = Math.round(startSeconds + deltaPx / pxPerSecond);
  return Math.min(MAX_SECONDS, Math.max(MIN_SECONDS, raw));
}

/** Which segment sits under a strip x-coordinate (width ∝ seconds). */
export function segmentIndexAtX(
  segments: TimelineSegment[],
  x: number,
  stripWidth: number,
): number {
  if (segments.length === 0) return 0;
  const total = totalSeconds(segments);
  if (total <= 0 || stripWidth <= 0) return 0;
  const targetSeconds = (Math.max(0, Math.min(stripWidth, x)) / stripWidth) * total;
  let acc = 0;
  for (let i = 0; i < segments.length; i += 1) {
    acc += segments[i].seconds;
    if (targetSeconds < acc) return i;
  }
  return segments.length - 1;
}
