// features/canvas-core/smart/nodes/TimelineNodeView.tsx
//
// Timeline director node (G8-F1 — Infinite's LTX director UX, adapted):
// a horizontal strip of segment blocks (width ∝ seconds), click a block to
// edit its prompt/length below, Run hands the whole timeline to the
// backend film workflow (t2v → tail-frame i2v chain → concat).
// Minimal set (P2-1): drag a block's right edge to resize its seconds,
// drag a block to reorder, tail-frame thumbnails, live "Segment i/N"
// progress and the failed-segment mark.

import { mediaSrc } from '../mediaUrl';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Plus, X } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

import { RUN_STATUS_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { RunStatusBadge } from './RunStatusBadge';
import {
  addSegment,
  dragSeconds,
  removeSegment,
  reorderSegments,
  reorderThumbs,
  segmentIndexAtX,
  totalSeconds,
  updateSegment,
  MAX_TIMELINE_SEGMENTS,
  type TimelineNodeData,
  type TimelineSegment,
} from '../timeline';
import { startTimelineRun, useTimelineRunStore } from '../timelineRun';
import { runSegmentClip } from '../clipRun';
import { playableClips, setSegmentRef } from '../timeline';
import { resolveSourceUrls } from '../promptInputs';
import { downloadUrl } from '../downloadMedia';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { ASPECT_RATIOS } from '../aspectPresets';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';
import { UiSelect } from '../../../../components/ui';

/** Movement below this many px stays a click (select-to-edit). */
const REORDER_THRESHOLD_PX = 6;

export function TimelineNodeView({ id, data, selected }: NodeProps) {
  const {
    segments = [],
    aspect = '',
    run_status = 'idle',
    run_error = null,
    run_progress = null,
    failed_index = null,
    segment_thumbs = [],
  } = data as unknown as TimelineNodeData;
  const patch = useNodeDataPatch(id);
  // Read-only: everything that reshapes the strip goes — edge-resize,
  // drag-reorder, add / remove segment, the Delete-key shortcut, the
  // seconds + prompt editors, the aspect picker and Run. Clicking a block
  // to inspect its prompt is view-only and stays.
  const readOnly = useCanvasReadOnly();
  const [clipRunning, setClipRunning] = useState<string | null>(null);
  const [playAllIndex, setPlayAllIndex] = useState<number | null>(null);
  const clips = playableClips(segments);
  const [clipError, setClipError] = useState<string | null>(null);
  const storeNodes = useCanvasCoreStore((st) => st.nodes);
  const storeConnections = useCanvasCoreStore((st) => st.connections);
  const upstreamImages = useMemo(
    () => resolveSourceUrls(id, storeNodes as never, storeConnections as never),
    [id, storeNodes, storeConnections],
  );
  const generateClip = (segId: string) => {
    setClipError(null);
    setClipRunning(segId);
    void runSegmentClip(id, segId).then((out) => {
      setClipRunning(null);
      if (!out.ok) setClipError(out.error ?? 'clip generation failed');
    });
  };
  const running = useTimelineRunStore((s) => !!s.running[id]) || run_status === 'running' || run_status === 'queued';
  const [activeId, setActiveId] = useState<string | null>(segments[0]?.id ?? null);
  const active = segments.find((s) => s.id === activeId) ?? null;
  const total = totalSeconds(segments);
  const stripRef = useRef<HTMLDivElement | null>(null);

  // ── Edge resize (P2-1): px-per-second is frozen at drag start so the
  // live width feedback doesn't make the scale chase itself. ──────────────
  const resizeDrag = useRef<{
    segId: string;
    startX: number;
    startSeconds: number;
    pxPerSecond: number;
  } | null>(null);

  const onResizeDown = (seg: TimelineSegment) => (e: React.PointerEvent) => {
    if (readOnly) return;
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    const stripWidth = stripRef.current?.getBoundingClientRect().width ?? 0;
    resizeDrag.current = {
      segId: seg.id,
      startX: e.clientX,
      startSeconds: seg.seconds,
      pxPerSecond: total > 0 ? stripWidth / total : 0,
    };
  };

  const onResizeMove = (e: React.PointerEvent) => {
    const d = resizeDrag.current;
    if (!d) return;
    e.preventDefault();
    e.stopPropagation();
    const next = dragSeconds(d.startSeconds, e.clientX - d.startX, d.pxPerSecond);
    const current = segments.find((s) => s.id === d.segId)?.seconds;
    if (next !== current) {
      patch({ segments: updateSegment(segments, d.segId, { seconds: next }) });
    }
  };

  const onResizeEnd = (e: React.PointerEvent) => {
    if (resizeDrag.current) e.stopPropagation();
    resizeDrag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  };

  // ── Drag reorder (P2-1): a horizontal pull past the threshold turns the
  // press into a reorder; releasing over a sibling moves the segment there.
  // A no-move press stays a click (select-to-edit). ────────────────────────
  const reorderDrag = useRef<{ segId: string; startX: number; moved: boolean } | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);

  const onBlockDown = (seg: TimelineSegment) => (e: React.PointerEvent) => {
    if (readOnly) return;
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    reorderDrag.current = { segId: seg.id, startX: e.clientX, moved: false };
  };

  const onBlockMove = (e: React.PointerEvent) => {
    const d = reorderDrag.current;
    if (!d) return;
    if (!d.moved && Math.abs(e.clientX - d.startX) < REORDER_THRESHOLD_PX) return;
    d.moved = true;
    const rect = stripRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    setDropIndex(segmentIndexAtX(segments, e.clientX - rect.left, rect.width));
  };

  const onBlockUp = (seg: TimelineSegment) => (e: React.PointerEvent) => {
    const d = reorderDrag.current;
    reorderDrag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    setDropIndex(null);
    if (!d || !d.moved) return;
    const rect = stripRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    const from = segments.findIndex((s) => s.id === seg.id);
    const to = segmentIndexAtX(segments, e.clientX - rect.left, rect.width);
    const next = reorderSegments(segments, from, to);
    if (next !== segments) {
      patch({
        segments: next,
        // Thumbnails show a segment's CONTENT — they travel with it.
        segment_thumbs: reorderThumbs(segment_thumbs, segments.length, from, to),
      });
    }
  };
  // Border colour only — the badge's dot carries the motion (P1-5).
  const tone = (RUN_STATUS_TONE[run_status] ?? 'border-canvas-line')
    .replace('animate-pulse', '')
    .trim();

  // Delete/Backspace with a segment focused removes THAT segment instead of
  // letting React Flow delete the whole node (P3-B). Guarded to when the
  // keystroke isn't inside a text field and there's more than one segment.
  const onNodeKeyDown = (e: React.KeyboardEvent) => {
    if (readOnly) return;
    if (e.key !== 'Delete' && e.key !== 'Backspace') return;
    if (!activeId || segments.length <= 1) return;
    const target = e.target as HTMLElement;
    const tag = target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable) return;
    e.preventDefault();
    e.stopPropagation();
    const next = removeSegment(segments, activeId);
    patch({ segments: next });
    setActiveId(next[0]?.id ?? null);
  };

  return (
    <div
      data-testid="smart-timeline-node"
      className={`mh-node ${tone} ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.timeline ?? 420 }}
      onKeyDown={onNodeKeyDown}
    >
      <Handle type="target" position={Position.Left} />
      <div className="mh-node-head">
        <div className="mh-node-title">Timeline</div>
        <div className="flex items-center gap-1.5">
          <RunStatusBadge status={run_status} />
          <UiSelect
            triggerClassName="nodrag mh-chip outline-none focus-visible:ring-1 focus-visible:ring-canvas-strong/40"
            value={aspect}
            onChange={(e) => patch({ aspect: e.target.value })}
            aria-label="Aspect ratio"
            disabled={readOnly}
          >
            {/* Auto (empty) + the shared composer presets — kept in lockstep
                with the Prompt node via ASPECT_RATIOS (P2). */}
            <option value="">Auto</option>
            {ASPECT_RATIOS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </UiSelect>
        </div>
      </div>

      <div className="p-3">
        {/* Segment strip — block width ∝ seconds. */}
        <div className="flex h-14 w-full gap-1" data-testid="timeline-strip" ref={stripRef}>
          {segments.map((seg, i) => (
            <button
              key={seg.id}
              type="button"
              data-testid={`timeline-seg-${seg.id}`}
              data-failed={failed_index === i ? 'true' : undefined}
              onClick={() => setActiveId(seg.id)}
              onPointerDown={onBlockDown(seg)}
              onPointerMove={onBlockMove}
              onPointerUp={onBlockUp(seg)}
              onPointerCancel={onBlockUp(seg)}
              style={{ flexGrow: seg.seconds, flexBasis: 0 }}
              className={`nodrag relative min-w-6 touch-none overflow-hidden rounded-lg border px-1.5 py-1 text-left transition-colors ${
                failed_index === i
                  ? 'border-rose-400/80 bg-rose-400/15'
                  : seg.id === activeId
                    ? 'border-canvas-strong bg-canvas-line/40'
                    : dropIndex === i
                      ? 'border-canvas-strong/60 bg-canvas-line/30'
                      : 'border-canvas-line bg-canvas-line/15 hover:bg-canvas-line/30'
              }`}
              title={
                failed_index === i
                  ? `Segment ${i + 1} failed — ${seg.prompt || 'empty'}`
                  : `${seg.seconds}s — ${seg.prompt || 'empty'}`
              }
            >
              {/* Tail-frame thumbnail underlay (P2-1). */}
              {segment_thumbs[i] && (
                <img
                  data-testid={`timeline-thumb-${i}`}
                  src={mediaSrc(segment_thumbs[i])}
                  alt=""
                  draggable={false}
                  className="pointer-events-none absolute inset-0 h-full w-full object-cover opacity-40"
                  onError={(e) => {
                    // A purged/stale durable URL must not leave a broken
                    // glyph under the label.
                    e.currentTarget.style.display = 'none';
                  }}
                />
              )}
              <div className="relative text-[9px] font-bold uppercase tracking-wider text-canvas-muted">
                {i + 1} · {seg.seconds}s{failed_index === i ? ' · ✕' : ''}
              </div>
              <div className="relative truncate text-[10px] text-canvas-text">
                {seg.prompt || '—'}
              </div>
              {/* Right-edge resize grip (P2-1): drag to change seconds. */}
              {/* The grip is pure affordance — with resize withdrawn it
                  would be a handle that doesn't handle, so it goes. */}
              {!readOnly && (
                <span
                  data-testid={`timeline-resize-${seg.id}`}
                  role="presentation"
                  onPointerDown={onResizeDown(seg)}
                  onPointerMove={onResizeMove}
                  onPointerUp={onResizeEnd}
                  onPointerCancel={onResizeEnd}
                  className="absolute inset-y-0 right-0 w-2 cursor-ew-resize touch-none rounded-r-lg hover:bg-canvas-strong/30"
                />
              )}
            </button>
          ))}
          {segments.length < MAX_TIMELINE_SEGMENTS && (
            <button
              type="button"
              aria-label="Add segment"
              onClick={() => patch({ segments: addSegment(segments) })}
              disabled={readOnly}
              className="nodrag flex w-7 shrink-0 items-center justify-center rounded-lg border border-dashed border-canvas-line text-canvas-muted hover:border-canvas-line-strong hover:text-canvas-text disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Plus size={12} />
            </button>
          )}
        </div>

        {/* Active segment editor. */}
        {active && (
          <div className="mt-2 rounded-lg border border-canvas-line p-2">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase tracking-wider text-canvas-muted">
                Segment {segments.findIndex((s) => s.id === active.id) + 1}
              </span>
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={active.seconds}
                  aria-label="Segment seconds"
                  disabled={readOnly}
                  onChange={(e) =>
                    patch({
                      segments: updateSegment(segments, active.id, {
                        seconds: Number(e.target.value),
                      }),
                    })
                  }
                  className="nodrag w-12 rounded-full border border-canvas-line bg-transparent px-2 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
                />
                <span className="text-[10px] text-canvas-muted">s</span>
                {segments.length > 1 && (
                  <button
                    type="button"
                    aria-label="Remove segment"
                    disabled={readOnly}
                    onClick={() => {
                      const next = removeSegment(segments, active.id);
                      patch({ segments: next });
                      setActiveId(next[0]?.id ?? null);
                    }}
                    className="text-canvas-muted hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <X size={12} />
                  </button>
                )}
              </div>
            </div>
            <textarea
              className="nodrag nowheel w-full resize-none bg-transparent text-xs text-canvas-text outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 read-only:opacity-80 read-only:cursor-default"
              rows={2}
              placeholder="Segment prompt…"
              value={active.prompt}
              onChange={(e) =>
                patch({ segments: updateSegment(segments, active.id, { prompt: e.target.value }) })
              }
              aria-label="Segment prompt"
              readOnly={readOnly}
            />
            {/* Per-clip reference frame (IC Refs, M1 simple form): pick one
                of the wired upstream images; the chosen frame drives i2v. */}
            {upstreamImages.length > 0 && (
              <div
                className="mt-1.5 flex items-center gap-1.5"
                data-testid="clip-ref-row"
              >
                <span className="text-[9px] font-bold uppercase tracking-wider text-canvas-muted">
                  Ref
                </span>
                {upstreamImages.slice(0, 6).map((url) => {
                  const selected = (active.ref_url ?? upstreamImages[0]) === url;
                  return (
                    <button
                      key={url}
                      type="button"
                      data-testid="clip-ref-thumb"
                      title={selected ? 'Reference frame' : 'Use as reference'}
                      disabled={readOnly}
                      onClick={() =>
                        patch({
                          segments: setSegmentRef(
                            segments,
                            active.id,
                            active.ref_url === url ? null : url,
                          ),
                        })
                      }
                      className={`nodrag h-6 w-6 shrink-0 overflow-hidden rounded border ${
                        selected
                          ? 'border-canvas-strong ring-1 ring-canvas-strong'
                          : 'border-canvas-line/60'
                      }`}
                    >
                      <img
                        src={mediaSrc(url)}
                        alt="Reference"
                        className="h-full w-full object-cover"
                      />
                    </button>
                  );
                })}
              </div>
            )}
            {/* Clip result player (IC's player stage, per-clip form).
                Play-all chains clips via onEnded (M2). */}
            {(active.result_url || playAllIndex !== null) && (
              <video
                key={playAllIndex !== null ? clips[playAllIndex]?.id : active.id}
                data-testid="clip-player"
                src={mediaSrc(
                  playAllIndex !== null
                    ? clips[playAllIndex]?.url ?? ''
                    : active.result_url ?? '',
                )}
                controls
                autoPlay={playAllIndex !== null}
                preload="metadata"
                onEnded={() => {
                  if (playAllIndex === null) return;
                  setPlayAllIndex(
                    playAllIndex + 1 < clips.length ? playAllIndex + 1 : null,
                  );
                }}
                className="nodrag nowheel mt-1.5 max-h-40 w-full rounded-lg bg-black/60"
              />
            )}
            {clipError && clipRunning === null && (
              <div
                data-testid="clip-error"
                role="alert"
                className="mt-1.5 rounded bg-rose-400/10 px-2 py-1 text-[10px] text-rose-500"
              >
                {clipError}
              </div>
            )}
            <div className="mt-1.5 flex items-center gap-1.5">
              {clips.length > 1 && (
                <button
                  type="button"
                  data-testid="play-all"
                  onClick={() =>
                    setPlayAllIndex(playAllIndex === null ? 0 : null)
                  }
                  className="nodrag rounded-full border border-canvas-line px-2.5 py-0.5 text-xs text-canvas-text"
                >
                  {playAllIndex === null ? 'Play all' : 'Stop'}
                </button>
              )}
              {active.result_url && (
                <button
                  type="button"
                  data-testid="download-clip"
                  onClick={() =>
                    void downloadUrl(
                      { url: active.result_url!, name: `clip-${active.id}.mp4` },
                      0,
                    )
                  }
                  className="nodrag rounded-full border border-canvas-line px-2.5 py-0.5 text-xs text-canvas-text"
                >
                  Download clip
                </button>
              )}
              <button
                type="button"
                data-testid="generate-clip"
                onClick={() => generateClip(active.id)}
                disabled={readOnly || clipRunning !== null}
                className="nodrag ml-auto flex items-center gap-1 rounded-full border border-transparent bg-canvas-strong px-2.5 py-0.5 text-xs font-bold text-canvas-card hover:opacity-90 disabled:opacity-40"
              >
                {clipRunning === active.id ? 'Generating…' : 'Generate clip'}
              </button>
            </div>
          </div>
        )}

        {/* Live per-segment progress (P2-1) — mirrored from the film
            task's metadata (segments_done / segments_total). */}
        {running && run_progress && run_progress.total > 0 && (
          <div
            data-testid="timeline-progress"
            className="mt-2 text-[10px] font-bold uppercase tracking-wider text-canvas-muted"
          >
            {run_progress.done >= run_progress.total
              ? 'Stitching film…'
              : `Segment ${run_progress.done + 1}/${run_progress.total}`}
          </div>
        )}

        {run_error && (
          <div role="alert" className="mt-2 text-[11px] text-rose-400">
            {run_error}
          </div>
        )}

        <div className="mt-2 flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wider text-canvas-muted">
            {total}s total · {segments.length} segment{segments.length === 1 ? '' : 's'}
          </span>
          <button
            type="button"
            onClick={() => void startTimelineRun(id)}
            data-testid="timeline-run"
            disabled={readOnly || running || segments.every((s) => !s.prompt.trim())}
            className="mh-chip !border-canvas-line-strong !text-canvas-text disabled:opacity-50"
          >
            {running ? 'Running…' : 'Run'}
          </button>
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
