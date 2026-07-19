/**
 * ArrangementView — the Beats "Arrangement" timeline editor (M2).
 *
 * Arranged beats (start_sec != null) render as absolutely-positioned cards on a
 * horizontal time ruler; overlapping cards stack into lanes. Dragging a card
 * body moves its start; dragging the right edge resizes its duration. Both snap
 * to the ruler grid on release and persist via `onUpdate`. Unarranged beats
 * (start_sec == null — legacy list-mode data) sit in a tray below and join the
 * timeline via Place. A left rail lists every beat and stays in two-way sync
 * with the card selection.
 *
 * All timeline math lives in arrangementGeometry (pure, separately tested); this
 * component is the render + pointer shell. Drag uses pointer events with pointer
 * capture (NOT HTML5 drag-and-drop — see #1389): only local state moves during
 * a drag, and the network write fires once on pointer-up, skipped if nothing
 * actually changed.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Beat, BeatInput } from '../sceneService';
import { formatBeatDuration } from './beatColors';
import {
  DEFAULT_PX_PER_SEC,
  MIN_CARD_PX,
  assignLanes,
  buildTicks,
  cardWidthPx,
  chooseTickUnit,
  computeTotalSec,
  fitPxPerSec,
  pxToTime,
  snapGranularity,
  snapSec,
  stepPxPerSec,
  timeToPx,
} from './arrangementGeometry';
import { persistBeatsZoom, readStoredBeatsZoom } from './beatsZoomStorage';

interface Props {
  scriptId: string;
  beats: Beat[];
  onAdd: () => void;
  onUpdate: (beatId: string, data: BeatInput) => void;
  onCreate: (data: BeatInput) => void;
}

/** Lane geometry (px) — pure layout constants, not stored data. */
const LANE_HEIGHT = 52;
const LANE_GAP = 8;
const LANES_PAD_TOP = 8;
const FLASH_MS = 700;

type DragMode = 'move' | 'resize';

interface DragState {
  id: string;
  mode: DragMode;
  start_sec: number;
  duration_sec: number | null;
}

interface DragContext extends DragState {
  pointerId: number;
  originClientX: number;
  origStart: number;
  origDuration: number | null;
  changed: boolean;
}

export function ArrangementView({ scriptId, beats, onAdd, onUpdate, onCreate }: Props) {
  const { t } = useTranslation();

  const [pxPerSec, setPxPerSec] = useState<number>(
    () => readStoredBeatsZoom(scriptId) ?? DEFAULT_PX_PER_SEC,
  );
  const applyZoom = useCallback(
    (next: number) => {
      setPxPerSec(next);
      persistBeatsZoom(scriptId, next);
    },
    [scriptId],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  const viewportRef = useRef<HTMLDivElement | null>(null);
  const lanesRef = useRef<HTMLDivElement | null>(null);
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const dragRef = useRef<DragContext | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    },
    [],
  );

  // Effective placement folds the in-flight drag over the stored value so the
  // card tracks the pointer without a network round-trip mid-drag.
  const placementOf = useCallback(
    (beat: Beat): { start_sec: number | null; duration_sec: number | null } => {
      if (drag && drag.id === beat.id) {
        return { start_sec: drag.start_sec, duration_sec: drag.duration_sec };
      }
      return { start_sec: beat.start_sec, duration_sec: beat.duration_sec };
    },
    [drag],
  );

  // If the dragged beat vanishes mid-drag (reload/delete), its card unmounts
  // with the pointer listeners, so endDrag can never fire — reset the machine.
  useEffect(() => {
    const ctx = dragRef.current;
    if (ctx && !beats.some((b) => b.id === ctx.id)) {
      dragRef.current = null;
      setDrag(null);
    }
  }, [beats]);

  const arranged = useMemo(
    () => beats.filter((b) => placementOf(b).start_sec != null),
    [beats, placementOf],
  );
  const unarranged = useMemo(
    () => beats.filter((b) => placementOf(b).start_sec == null),
    [beats, placementOf],
  );

  const totalSec = useMemo(
    () => computeTotalSec(arranged.map((b) => placementOf(b))),
    [arranged, placementOf],
  );
  const unit = chooseTickUnit(totalSec);
  const granularity = snapGranularity(unit);
  const ticks = useMemo(() => buildTicks(totalSec, unit), [totalSec, unit]);

  const lanes = useMemo(() => {
    const minDurationSec = pxToTime(MIN_CARD_PX, pxPerSec);
    return assignLanes(
      arranged.map((b) => {
        const p = placementOf(b);
        return { id: b.id, start_sec: p.start_sec ?? 0, duration_sec: p.duration_sec };
      }),
      minDurationSec,
    );
  }, [arranged, placementOf, pxPerSec]);
  const laneCount = useMemo(() => {
    let max = 0;
    for (const lane of lanes.values()) if (lane > max) max = lane;
    return max + 1;
  }, [lanes]);

  const canvasWidth = timeToPx(totalSec, pxPerSec);
  const lanesHeight = LANES_PAD_TOP + laneCount * (LANE_HEIGHT + LANE_GAP);

  // ── Selection + left-rail sync ────────────────────────────────────────────
  const focusBeat = useCallback((beatId: string) => {
    setSelectedId(beatId);
    const el = cardRefs.current.get(beatId);
    if (el) {
      // Optional-call: jsdom has no scrollIntoView; centering is best-effort.
      el.scrollIntoView?.({ inline: 'center', block: 'nearest', behavior: 'smooth' });
      setFlashId(beatId);
      if (flashTimer.current) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setFlashId(null), FLASH_MS);
    }
  }, []);

  // ── Pointer drag (move / resize) ──────────────────────────────────────────
  const beginDrag = useCallback(
    (e: React.PointerEvent, beat: Beat, mode: DragMode) => {
      if (e.button !== 0) return;
      // One drag at a time: a second touch mid-drag would hijack dragRef, strand
      // the first pointer's capture and silently drop its pending write.
      if (dragRef.current) return;
      const p = placementOf(beat);
      const card = cardRefs.current.get(beat.id);
      try {
        card?.setPointerCapture(e.pointerId);
      } catch {
        // jsdom / stale pointer — capture is best-effort, drag still works.
      }
      dragRef.current = {
        id: beat.id,
        mode,
        pointerId: e.pointerId,
        originClientX: e.clientX,
        origStart: p.start_sec ?? 0,
        origDuration: p.duration_sec,
        start_sec: p.start_sec ?? 0,
        duration_sec: p.duration_sec,
        changed: false,
      };
      setSelectedId(beat.id);
      setDrag({ id: beat.id, mode, start_sec: p.start_sec ?? 0, duration_sec: p.duration_sec });
      e.preventDefault();
      e.stopPropagation();
    },
    [placementOf],
  );

  const moveDrag = useCallback(
    (e: React.PointerEvent) => {
      const ctx = dragRef.current;
      if (!ctx || e.pointerId !== ctx.pointerId) return;
      const dSec = pxToTime(e.clientX - ctx.originClientX, pxPerSec);
      if (ctx.mode === 'move') {
        const next = snapSec(ctx.origStart + dSec, granularity);
        if (next === ctx.start_sec) return;
        ctx.start_sec = next;
        ctx.changed = true;
        setDrag({ id: ctx.id, mode: 'move', start_sec: next, duration_sec: ctx.duration_sec });
      } else {
        const base = ctx.origDuration ?? granularity;
        const next = Math.max(granularity, snapSec(base + dSec, granularity));
        if (next === ctx.duration_sec) return;
        ctx.duration_sec = next;
        ctx.changed = true;
        setDrag({ id: ctx.id, mode: 'resize', start_sec: ctx.start_sec, duration_sec: next });
      }
    },
    [pxPerSec, granularity],
  );

  const endDrag = useCallback(
    (e: React.PointerEvent) => {
      const ctx = dragRef.current;
      if (!ctx || e.pointerId !== ctx.pointerId) return;
      const card = cardRefs.current.get(ctx.id);
      try {
        card?.releasePointerCapture(e.pointerId);
      } catch {
        /* best-effort */
      }
      // State unchanged → bail without a network write (drag discipline #1389).
      if (ctx.changed) {
        if (ctx.mode === 'move') onUpdate(ctx.id, { start_sec: ctx.start_sec });
        else onUpdate(ctx.id, { duration_sec: ctx.duration_sec });
      }
      dragRef.current = null;
      setDrag(null);
    },
    [onUpdate],
  );

  // ── Zoom ──────────────────────────────────────────────────────────────────
  const zoomIn = useCallback(() => applyZoom(stepPxPerSec(pxPerSec, 1)), [applyZoom, pxPerSec]);
  const zoomOut = useCallback(() => applyZoom(stepPxPerSec(pxPerSec, -1)), [applyZoom, pxPerSec]);
  const fitView = useCallback(() => {
    const width = viewportRef.current?.clientWidth ?? 0;
    applyZoom(fitPxPerSec(totalSec, width));
  }, [applyZoom, totalSec]);

  // Ctrl/Cmd+wheel zoom needs a NATIVE non-passive listener: React ≥17 registers
  // root wheel handlers as passive, so preventDefault() in onWheel is a no-op and
  // the browser page-zooms alongside the timeline (same fix as ActivityPanel).
  const wheelZoomRef = useRef<(e: WheelEvent) => void>(() => {});
  wheelZoomRef.current = (e: WheelEvent) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    applyZoom(stepPxPerSec(pxPerSec, e.deltaY < 0 ? 1 : -1));
  };
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => wheelZoomRef.current(e);
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  // ── Double-click empty timeline → new beat at that instant ──────────────────
  const onLanesDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      if ((e.target as HTMLElement).closest('.mh-arr-card')) return;
      const rect = lanesRef.current?.getBoundingClientRect();
      const x = rect ? e.clientX - rect.left : 0;
      const sec = snapSec(pxToTime(x, pxPerSec), granularity);
      onCreate({ start_sec: sec, duration_sec: 60 });
    },
    [onCreate, pxPerSec, granularity],
  );

  // ── Place an unarranged beat at the end of the arranged timeline ────────────
  const placeBeat = useCallback(
    (beat: Beat) => {
      const end = arranged.reduce((max, b) => {
        const p = placementOf(b);
        const e = (p.start_sec ?? 0) + (p.duration_sec ?? 0);
        return e > max ? e : max;
      }, 0);
      onUpdate(beat.id, { start_sec: snapSec(end, granularity) });
    },
    [arranged, placementOf, onUpdate, granularity],
  );

  const setCardRef = useCallback(
    (id: string) => (el: HTMLDivElement | null) => {
      if (el) cardRefs.current.set(id, el);
      else cardRefs.current.delete(id);
    },
    [],
  );

  const registerLanes = useCallback((el: HTMLDivElement | null) => {
    lanesRef.current = el;
  }, []);

  if (beats.length === 0) {
    return (
      <div className="mh-arr-root" data-testid="beats-arrangement">
        <div className="mh-beats-empty" data-testid="beats-arrangement-empty">
          <div className="mh-beats-empty-title">{t('editor.beatsEmptyTitle')}</div>
          <p className="mh-beats-empty-sub">{t('editor.arrEmptySub')}</p>
          <button type="button" className="mh-beats-add-btn" data-testid="beats-add" onClick={onAdd}>
            {t('editor.beatAdd')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mh-arr-root" data-testid="beats-arrangement">
      <div className="mh-arr-body">
        <aside className="mh-arr-list" aria-label={t('editor.arrBeatList')}>
          {beats.map((beat, index) => (
            <button
              key={beat.id}
              type="button"
              className={`mh-arr-list-item${selectedId === beat.id ? ' selected' : ''}`}
              data-testid="arr-list-item"
              data-beat-id={beat.id}
              onClick={() => focusBeat(beat.id)}
            >
              <span className="mh-arr-list-num" aria-hidden="true">
                {index + 1}
              </span>
              <span className="mh-arr-list-title">
                {beat.title || t('editor.beatTitlePlaceholder')}
              </span>
            </button>
          ))}
        </aside>

        <div className="mh-arr-timeline-scroll" ref={viewportRef} data-testid="arr-viewport">
          <div className="mh-arr-canvas" style={{ width: `${canvasWidth}px` }}>
            <div className="mh-arr-ruler" data-testid="arr-ruler">
              {ticks.map((tick) => (
                <div
                  key={tick.sec}
                  className={`mh-arr-tick${tick.major ? ' major' : ''}`}
                  style={{ left: `${timeToPx(tick.sec, pxPerSec)}px` }}
                >
                  {tick.label && <span className="mh-arr-tick-label">{tick.label}</span>}
                </div>
              ))}
            </div>

            <div
              className="mh-arr-lanes"
              ref={registerLanes}
              data-testid="arr-lanes"
              style={{ height: `${lanesHeight}px` }}
              onDoubleClick={onLanesDoubleClick}
            >
              {arranged.map((beat) => {
                const p = placementOf(beat);
                const lane = lanes.get(beat.id) ?? 0;
                const durationLabel = formatBeatDuration(p.duration_sec);
                return (
                  <div
                    key={beat.id}
                    ref={setCardRef(beat.id)}
                    className={`mh-arr-card${selectedId === beat.id ? ' selected' : ''}${
                      flashId === beat.id ? ' flash' : ''
                    }${drag?.id === beat.id ? ' dragging' : ''}`}
                    data-testid="arr-card"
                    data-beat-id={beat.id}
                    style={{
                      left: `${timeToPx(p.start_sec ?? 0, pxPerSec)}px`,
                      width: `${cardWidthPx(p.duration_sec, pxPerSec)}px`,
                      top: `${LANES_PAD_TOP + lane * (LANE_HEIGHT + LANE_GAP)}px`,
                    }}
                    onPointerDown={(e) => beginDrag(e, beat, 'move')}
                    onPointerMove={moveDrag}
                    onPointerUp={endDrag}
                    onPointerCancel={endDrag}
                    onClick={() => setSelectedId(beat.id)}
                  >
                    <span
                      className="mh-arr-card-strip"
                      aria-hidden="true"
                      style={{ background: beat.color || 'var(--hairline)' }}
                    />
                    <span className="mh-arr-card-title">
                      {beat.title || t('editor.beatTitlePlaceholder')}
                    </span>
                    {durationLabel && <span className="mh-arr-card-chip">{durationLabel}</span>}
                    <span
                      className="mh-arr-card-resize"
                      data-testid="arr-card-resize"
                      aria-label={t('editor.arrResize')}
                      onPointerDown={(e) => beginDrag(e, beat, 'resize')}
                      onPointerMove={moveDrag}
                      onPointerUp={endDrag}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {unarranged.length > 0 && (
        <div className="mh-arr-tray" data-testid="arr-tray">
          <span className="mh-arr-tray-label">{t('editor.arrUnarranged')}</span>
          <div className="mh-arr-tray-cards">
            {unarranged.map((beat) => (
              <div
                key={beat.id}
                className="mh-arr-tray-card"
                data-testid="arr-tray-card"
                data-beat-id={beat.id}
              >
                <span
                  className="mh-arr-tray-strip"
                  aria-hidden="true"
                  style={{ background: beat.color || 'var(--hairline)' }}
                />
                <span className="mh-arr-tray-title">
                  {beat.title || t('editor.beatTitlePlaceholder')}
                </span>
                <button
                  type="button"
                  className="mh-arr-tray-place"
                  data-testid="arr-tray-place"
                  onClick={() => placeBeat(beat)}
                >
                  {t('editor.arrPlace')}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mh-arr-tools" data-testid="arr-tools" role="group" aria-label={t('editor.arrZoomTools')}>
        <button
          type="button"
          className="mh-arr-tool-btn"
          data-testid="arr-zoom-out"
          aria-label={t('editor.arrZoomOut')}
          onClick={zoomOut}
        >
          −
        </button>
        <button
          type="button"
          className="mh-arr-tool-btn"
          data-testid="arr-zoom-in"
          aria-label={t('editor.arrZoomIn')}
          onClick={zoomIn}
        >
          +
        </button>
        <button
          type="button"
          className="mh-arr-tool-btn wide"
          data-testid="arr-fit"
          onClick={fitView}
        >
          {t('editor.arrFit')}
        </button>
      </div>
    </div>
  );
}
