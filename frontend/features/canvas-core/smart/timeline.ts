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
