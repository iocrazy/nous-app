// features/canvas-core/smart/timeline.ts
//
// Timeline director node (G8-F1 — Infinite's LTX director, adapted):
// a multi-segment film described on one node. Pure segment CRUD helpers;
// the run wiring lives in timelineRun.ts, the chrome in TimelineNodeView.

export interface TimelineSegment {
  id: string;
  prompt: string;
  seconds: number;
  /** M1 MiniMax workbench: the clip's generated video url (durable). */
  result_url?: string | null;
  /** M1: manual i2v reference frame for THIS clip (durable image url). */
  ref_url?: string | null;
  /** η2 (IC refItems): ordered multi-reference set — images now, video /
   *  audio kinds arrive with the ComfyUI engine. Supersedes ref_url. */
  ref_items?: Array<{ url: string; kind: string }>;
  /** η2 (IC trimIn/trimOut): playback window inside the generated clip,
   *  seconds from clip start. Clamped to [0, seconds], ≥0.1s span. */
  trim_in?: number;
  trim_out?: number;
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
  /** M1 workbench: the segment whose panel is open (click-to-select). */
  selected_segment?: string | null;
  /** Per-segment tail-frame thumbnails (P2-1) — durable /cover URLs,
   *  index-aligned with segments (the last segment has no tail; reorder
   *  pads with nulls to keep alignment). */
  segment_thumbs?: Array<string | null>;
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

/** Move one item to a new position. Out-of-range sources and same-index
 *  moves return the input untouched; targets clamp into range. */
export function reorderItems<T>(items: T[], fromIndex: number, toIndex: number): T[] {
  if (fromIndex < 0 || fromIndex >= items.length) return items;
  const target = Math.max(0, Math.min(items.length - 1, toIndex));
  if (target === fromIndex) return items;
  const next = [...items];
  const [moved] = next.splice(fromIndex, 1);
  next.splice(target, 0, moved);
  return next;
}

/** Move a segment to a new position (drag reorder). */
export function reorderSegments(
  segments: TimelineSegment[],
  fromIndex: number,
  toIndex: number,
): TimelineSegment[] {
  return reorderItems(segments, fromIndex, toIndex);
}

/** Tail-frame thumbnails travel WITH their segment on reorder — they show
 *  the segment's content, not its position. The sparse tail (last segment
 *  has no frame) pads to nulls so indices stay aligned. */
export function reorderThumbs(
  thumbs: Array<string | null>,
  segmentCount: number,
  fromIndex: number,
  toIndex: number,
): Array<string | null> {
  const padded = Array.from({ length: segmentCount }, (_, i) => thumbs[i] ?? null);
  return reorderItems(padded, fromIndex, toIndex);
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


// ── M1 MiniMax workbench: per-clip results ─────────────────────────────────

export function setSegmentResult(
  segments: TimelineSegment[],
  id: string,
  url: string | null,
): TimelineSegment[] {
  return segments.map((s) => (s.id === id ? { ...s, result_url: url } : s));
}

export function setSegmentRef(
  segments: TimelineSegment[],
  id: string,
  url: string | null,
): TimelineSegment[] {
  return segments.map((s) => (s.id === id ? { ...s, ref_url: url } : s));
}

/** Ordered playable clips (segments with a result), for play-all. */
export function playableClips(
  segments: TimelineSegment[],
): Array<{ id: string; url: string }> {
  return segments
    .filter((s): s is TimelineSegment & { result_url: string } =>
      Boolean(s.result_url),
    )
    .map((s) => ({ id: s.id, url: s.result_url }));
}

// ── η2: IC smartMinimaxEnsureSegment parity ─────────────────────────────────

const MIN_TRIM_SPAN = 0.1;

/** Normalize a segment: clamp trims into [0, seconds] with a ≥0.1s span and
 *  migrate the legacy single ref_url into ref_items (IC :7318-7357). */
export function ensureSegment(seg: TimelineSegment): TimelineSegment {
  const out: TimelineSegment = { ...seg };
  if (out.ref_url && !out.ref_items?.length) {
    out.ref_items = [{ url: out.ref_url, kind: 'image' }];
  }
  if (out.trim_in !== undefined || out.trim_out !== undefined) {
    let tin = Math.max(0, Math.min(out.trim_in ?? 0, out.seconds));
    let tout = Math.max(0, Math.min(out.trim_out ?? out.seconds, out.seconds));
    if (tout - tin < MIN_TRIM_SPAN) {
      tout = Math.min(out.seconds, tin + MIN_TRIM_SPAN);
      tin = Math.max(0, tout - MIN_TRIM_SPAN);
    }
    // Round to centiseconds — float subtraction otherwise yields 0.0999….
    out.trim_in = Math.round(tin * 100) / 100;
    out.trim_out = Math.round(tout * 100) / 100;
  }
  return out;
}

/** Back-to-back clip start times (IC start = prev start + duration). */
export function segmentStarts(segments: TimelineSegment[]): number[] {
  const starts: number[] = [];
  let acc = 0;
  for (const seg of segments) {
    starts.push(acc);
    acc += Math.max(0, seg.seconds);
  }
  return starts;
}

/** The clip under the playhead at `t` seconds (IC :7502); null past the end. */
export function activeSegmentAt(
  segments: TimelineSegment[],
  t: number,
): TimelineSegment | null {
  const starts = segmentStarts(segments);
  for (let i = segments.length - 1; i >= 0; i--) {
    if (t >= starts[i] && t < starts[i] + segments[i].seconds) return segments[i];
  }
  return null;
}
