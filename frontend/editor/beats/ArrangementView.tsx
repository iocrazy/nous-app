/**
 * ArrangementView — the Beats "Arrangement" timeline editor (M2, laper-aligned).
 *
 * A dot-grid canvas with tall white beat cards floating on a horizontal time
 * ruler (film-apostrophe minute labels); overlapping cards stack into lanes.
 * Dragging a card body moves its start; dragging the right edge resizes its
 * duration; "Edit Beat" opens a modal for title/summary/duration/color/scene
 * links. Unarranged beats (start_sec == null — legacy list data) sit in a dashed
 * tray and join via Place. A light left rail lists every beat in two-way sync
 * with the card selection. A second (empty) ruler below the cards is the visual
 * home for M4 memo pins — reserved here, no interaction yet.
 *
 * All timeline math lives in arrangementGeometry (pure, separately tested); this
 * component is the render + pointer shell. Drag uses pointer events with pointer
 * capture (NOT HTML5 drag-and-drop — see #1389): only local state moves during
 * a drag, and the network write fires once on pointer-up, skipped if nothing
 * actually changed. Zoom is cursor-anchored (dnd-timeline style).
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Beat, BeatInput } from '../sceneService';
import type { SceneDoc } from '../types';
import { formatBeatDuration } from './beatColors';
import { BeatEditModal } from './BeatEditModal';
import { BeatsDurationControl } from './BeatsDurationControl';
import { BeatsSaveTemplateModal } from './BeatsSaveTemplateModal';
import { BeatsTemplateWizard } from './BeatsTemplateWizard';
import type { CustomTemplate } from './beatTemplateService';
import {
  CARD_GAP_PX,
  DEFAULT_PX_PER_SEC,
  MIN_CARD_PX,
  anchorScrollLeft,
  conformBeats,
  layoutCards,
  layoutExtentPx,
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
import { MemoRail } from './MemoRail';
import { BEAT_TEMPLATES, deriveTemplateAnchors } from './templates';

interface Props {
  scriptId: string;
  beats: Beat[];
  scenes: SceneDoc[];
  /** Per-script target total runtime in seconds (M3); null = unset. */
  targetDurationSec: number | null;
  onAdd: () => void;
  onUpdate: (beatId: string, data: BeatInput) => void;
  onCreate: (data: BeatInput) => void;
  onOpenScene: (sceneId: string) => void;
  /** Persist a new target total length (PATCH script_projects). */
  onSetTargetDuration: (sec: number) => void;
  /** Apply a methodology template: batch-create the rows, optionally replacing
   *  the existing beats, and persist the chosen target length. */
  onApplyTemplate: (beats: BeatInput[], mode: 'append' | 'replace', targetSec: number) => void;
  /** The caller's saved custom templates, shown beside the built-in three. */
  customTemplates?: CustomTemplate[];
  /** Persist the current arrangement as a new custom template. */
  onSaveTemplate?: (name: string, anchors: ReturnType<typeof deriveTemplateAnchors>) => void;
  /** Delete a saved custom template by id. */
  onDeleteCustomTemplate?: (id: string) => void;
}

/** Card + lane geometry (px) — pure layout constants, not stored data. */
const CARD_HEIGHT = 128;
const LANE_GAP = 16;
const LANES_PAD_TOP = 12;
const FLASH_MS = 700;

type DragMode = 'move' | 'resize';

/** Guide readout: `1'30` past the minute, `45s` under it (film-apostrophe). */
function formatGuideSec(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s === 0 ? `${m}'` : `${m}'${String(s).padStart(2, '0')}`;
}

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

export function ArrangementView({
  scriptId,
  beats,
  scenes,
  targetDurationSec,
  onAdd,
  onUpdate,
  onCreate,
  onOpenScene,
  onSetTargetDuration,
  onApplyTemplate,
  customTemplates = [],
  onSaveTemplate,
  onDeleteCustomTemplate,
}: Props) {
  const { t } = useTranslation();

  // Whether zoom came from storage decides auto-fit: a first-ever open with no
  // stored zoom fits the whole timeline; once the user zooms, we persist.
  const storedZoom = useRef(readStoredBeatsZoom(scriptId));
  const [pxPerSec, setPxPerSec] = useState<number>(() => storedZoom.current ?? DEFAULT_PX_PER_SEC);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  // M3 overlays: the Apply-template wizard (with an optional preselected key),
  // the methodology Guide drawer, and the conform prompt shown when the target
  // length changes while beats are already arranged.
  const [wizardKey, setWizardKey] = useState<string | null | undefined>(undefined);
  const [conform, setConform] = useState<{ next: number; old: number } | null>(null);
  // M3.5: the "Save as template" name dialog.
  const [savingTemplate, setSavingTemplate] = useState(false);

  const viewportRef = useRef<HTMLDivElement | null>(null);
  const lanesRef = useRef<HTMLDivElement | null>(null);
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const dragRef = useRef<DragContext | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingScrollRef = useRef<number | null>(null);
  const autoFitDoneRef = useRef(false);

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
    () => computeTotalSec(arranged.map((b) => placementOf(b)), targetDurationSec),
    [arranged, placementOf, targetDurationSec],
  );
  const unit = chooseTickUnit(totalSec);
  const granularity = snapGranularity(unit);
  const ticks = useMemo(() => buildTicks(totalSec, unit), [totalSec, unit]);
  const majorTickSecs = useMemo(
    () => ticks.filter((tick) => tick.major).map((tick) => tick.sec),
    [ticks],
  );

  // Stable layout from the STORED placements (not the drag fold): the dragged
  // card tracks the pointer directly and must not shove its neighbours around
  // mid-drag — everyone else keeps their settled spot until pointer-up commits.
  const layout = useMemo(
    () =>
      layoutCards(
        arranged.map((b) => ({
          id: b.id,
          start_sec: b.start_sec ?? 0,
          duration_sec: b.duration_sec,
        })),
        pxPerSec,
      ),
    [arranged, pxPerSec],
  );
  const laneCount = useMemo(() => {
    let max = 0;
    for (const l of layout.values()) if (l.lane > max) max = l.lane;
    return max + 1;
  }, [layout]);

  const canvasWidth = layoutExtentPx(layout, totalSec, pxPerSec);
  const lanesHeight = LANES_PAD_TOP + laneCount * (CARD_HEIGHT + LANE_GAP);

  // ── Zoom (cursor-anchored) ──────────────────────────────────────────────
  // Keep the timeline point under `anchorClientX` pinned across the zoom by
  // pre-computing the compensating scrollLeft, then applying it after the width
  // re-renders (useLayoutEffect, before paint → no visible jump).
  const applyZoomAnchored = useCallback(
    (next: number, anchorClientX: number, persist: boolean) => {
      if (next === pxPerSec) return;
      const el = viewportRef.current;
      if (el) {
        const rect = el.getBoundingClientRect();
        pendingScrollRef.current = anchorScrollLeft({
          prevPxPerSec: pxPerSec,
          nextPxPerSec: next,
          anchorClientX,
          viewportLeft: rect.left,
          prevScrollLeft: el.scrollLeft,
        });
      }
      setPxPerSec(next);
      if (persist) {
        storedZoom.current = next;
        persistBeatsZoom(scriptId, next);
      }
    },
    [pxPerSec, scriptId],
  );

  useLayoutEffect(() => {
    if (pendingScrollRef.current != null && viewportRef.current) {
      viewportRef.current.scrollLeft = pendingScrollRef.current;
      pendingScrollRef.current = null;
    }
  }, [pxPerSec]);

  const viewportCenterX = useCallback((): number => {
    const el = viewportRef.current;
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    return rect.left + el.clientWidth / 2;
  }, []);

  const zoomIn = useCallback(
    () => applyZoomAnchored(stepPxPerSec(pxPerSec, 1), viewportCenterX(), true),
    [applyZoomAnchored, pxPerSec, viewportCenterX],
  );
  const zoomOut = useCallback(
    () => applyZoomAnchored(stepPxPerSec(pxPerSec, -1), viewportCenterX(), true),
    [applyZoomAnchored, pxPerSec, viewportCenterX],
  );
  const fitView = useCallback(() => {
    const width = viewportRef.current?.clientWidth ?? 0;
    const next = fitPxPerSec(totalSec, width);
    pendingScrollRef.current = 0; // fit shows the whole timeline from the start
    setPxPerSec(next);
    storedZoom.current = next;
    persistBeatsZoom(scriptId, next);
  }, [totalSec, scriptId]);

  // First open with no stored zoom → auto-fit once (not persisted; the writer's
  // first manual zoom starts persistence). The autoFitDoneRef + storedZoom guards
  // make this one-shot; depending on `beats` lets it retry if the rows (or the
  // measurable viewport width) only arrive after mount. In jsdom (width 0) it
  // no-ops, keeping seeded-zoom tests stable.
  useEffect(() => {
    if (autoFitDoneRef.current || storedZoom.current != null) return;
    const width = viewportRef.current?.clientWidth ?? 0;
    if (width <= 0) return;
    autoFitDoneRef.current = true;
    const total = computeTotalSec(
      beats
        .filter((b) => b.start_sec != null)
        .map((b) => ({ start_sec: b.start_sec, duration_sec: b.duration_sec })),
      targetDurationSec,
    );
    setPxPerSec(fitPxPerSec(total, width));
  }, [beats, targetDurationSec]);

  // Ctrl/Cmd+wheel zoom needs a NATIVE non-passive listener: React ≥17 registers
  // root wheel handlers as passive, so preventDefault() in onWheel is a no-op and
  // the browser page-zooms alongside the timeline (same fix as ActivityPanel).
  const wheelZoomRef = useRef<(e: WheelEvent) => void>(() => {});
  wheelZoomRef.current = (e: WheelEvent) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    applyZoomAnchored(stepPxPerSec(pxPerSec, e.deltaY < 0 ? 1 : -1), e.clientX, true);
  };
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => wheelZoomRef.current(e);
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

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

  // ── Target length + conform ────────────────────────────────────────────────
  // Committing a new target with beats already arranged AND a previous target
  // set prompts the conform choice; the first-ever set (old == null) or an empty
  // timeline just moves the ruler.
  const commitTarget = useCallback(
    (nextSec: number) => {
      if (arranged.length > 0 && targetDurationSec != null && targetDurationSec !== nextSec) {
        setConform({ next: nextSec, old: targetDurationSec });
      } else {
        onSetTargetDuration(nextSec);
      }
    },
    [arranged.length, targetDurationSec, onSetTargetDuration],
  );

  const resolveConform = useCallback(
    (stretch: boolean) => {
      if (!conform) return;
      const { next, old } = conform;
      if (stretch) {
        const g = snapGranularity(chooseTickUnit(Math.max(next, 60)));
        const scaled = conformBeats(
          arranged.map((b) => ({
            id: b.id,
            start_sec: b.start_sec,
            duration_sec: b.duration_sec,
          })),
          old,
          next,
          g,
        );
        for (const s of scaled) {
          onUpdate(s.id, { start_sec: s.start_sec, duration_sec: s.duration_sec });
        }
      }
      onSetTargetDuration(next);
      setConform(null);
    },
    [conform, arranged, onUpdate, onSetTargetDuration],
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

  const editingBeat = editingId ? beats.find((b) => b.id === editingId) ?? null : null;

  // Shared M3 overlays — rendered in both the empty and populated views so the
  // empty-state template cards and the topbar tools open the same modals.
  const overlays = (
    <>
      {wizardKey !== undefined && (
        <BeatsTemplateWizard
          initialTemplateKey={wizardKey}
          currentTargetSec={targetDurationSec}
          hasExistingBeats={beats.length > 0}
          customTemplates={customTemplates}
          onApply={onApplyTemplate}
          onDeleteCustom={onDeleteCustomTemplate}
          onClose={() => setWizardKey(undefined)}
        />
      )}
      {savingTemplate && onSaveTemplate && (
        <BeatsSaveTemplateModal
          onSave={(name) => {
            // Reverse-compute the anchors from the beats arranged RIGHT NOW
            // (relative to the current total) — the geometry owner is here.
            onSaveTemplate(name, deriveTemplateAnchors(arranged, totalSec));
            setSavingTemplate(false);
          }}
          onClose={() => setSavingTemplate(false)}
        />
      )}
    </>
  );

  if (beats.length === 0) {
    return (
      <div className="mh-arr-root" data-testid="beats-arrangement">
        <div className="mh-beats-empty" data-testid="beats-arrangement-empty">
          <div className="mh-beats-empty-title">{t('editor.beatsEmptyTitle')}</div>
          <p className="mh-beats-empty-sub">{t('editor.arrTemplateEmptySub')}</p>
          <div className="mh-tpl-empty-cards" data-testid="beats-template-empty-cards">
            {BEAT_TEMPLATES.map((tpl) => (
              <button
                key={tpl.key}
                type="button"
                className="mh-tpl-empty-card"
                data-testid="beats-template-empty-card"
                data-key={tpl.key}
                onClick={() => setWizardKey(tpl.key)}
              >
                <span className="mh-tpl-card-name">{t(tpl.nameKey)}</span>
                <span className="mh-tpl-card-desc">{t(tpl.descKey)}</span>
                <span className="mh-tpl-card-count">
                  {t('editor.beatTplBeatCount', { count: tpl.beats.length })}
                </span>
              </button>
            ))}
          </div>
          {customTemplates.length > 0 && (
            <div className="mh-tpl-empty-custom" data-testid="beats-template-empty-custom">
              <span className="mh-tpl-empty-custom-label">{t('editor.beatTplCustomHeading')}</span>
              <div className="mh-tpl-empty-custom-cards">
                {customTemplates.map((ct) => (
                  <button
                    key={ct.id}
                    type="button"
                    className="mh-tpl-empty-custom-card"
                    data-testid="beats-template-empty-custom-card"
                    data-id={ct.id}
                    onClick={() => setWizardKey(`custom:${ct.id}`)}
                  >
                    <span className="mh-tpl-card-name">{ct.name}</span>
                    <span className="mh-tpl-card-count">
                      {t('editor.beatTplBeatCount', { count: ct.anchors.length })}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {/* One quiet fallback below the template cards — a hairline "or" rule
              then a ghost Add, so the loud action is picking a methodology, not a
              blank beat. Single Add construct (topbar Add is hidden when empty). */}
          <div className="mh-beats-empty-or" aria-hidden="true">
            <span>{t('editor.beatEmptyOr')}</span>
          </div>
          <button
            type="button"
            className="mh-beats-add-ghost"
            data-testid="beats-add"
            onClick={onAdd}
          >
            {t('editor.beatAdd')}
          </button>
        </div>
        {overlays}
      </div>
    );
  }

  return (
    <div className="mh-arr-root" data-testid="beats-arrangement">
      {/* Toolbar row above the canvas — the pill floated over the ruler labels
          when absolute-positioned, so it lives in normal flow up here. Left
          group = target length + methodology tools; right group = zoom. */}
      <div className="mh-arr-topbar">
        <div className="mh-arr-tools" data-testid="arr-tools-left" role="group">
          <BeatsDurationControl targetSec={targetDurationSec} onCommit={commitTarget} />
          <span className="mh-arr-tool-sep" aria-hidden="true" />
          <button
            type="button"
            className="mh-arr-tool-btn"
            data-testid="arr-templates"
            onClick={() => setWizardKey(null)}
          >
            {t('editor.beatTemplatesBtn')}
          </button>
          {onSaveTemplate && (
            <button
              type="button"
              className="mh-arr-tool-btn"
              data-testid="arr-save-template"
              disabled={arranged.length === 0}
              onClick={() => setSavingTemplate(true)}
            >
              {t('editor.beatSaveTemplateBtn')}
            </button>
          )}
        </div>
        <div
          className="mh-arr-tools"
          data-testid="arr-tools"
          role="group"
          aria-label={t('editor.arrZoomTools')}
        >
          <button type="button" className="mh-arr-tool-btn" data-testid="arr-fit" onClick={fitView}>
            {t('editor.arrFit')}
          </button>
          <span className="mh-arr-tool-sep" aria-hidden="true" />
          <button
            type="button"
            className="mh-arr-tool-btn"
            data-testid="arr-zoom-out"
            aria-label={t('editor.arrZoomOut')}
            onClick={zoomOut}
          >
            − {t('editor.arrZoomOut')}
          </button>
          <button
            type="button"
            className="mh-arr-tool-btn"
            data-testid="arr-zoom-in"
            aria-label={t('editor.arrZoomIn')}
            onClick={zoomIn}
          >
            + {t('editor.arrZoomIn')}
          </button>
        </div>
      </div>

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
              <span className="mh-arr-list-grip" aria-hidden="true">
                ⋮
              </span>
              <span className="mh-arr-list-num" aria-hidden="true">
                {index + 1}
              </span>
              <span className="mh-arr-list-title">
                {beat.title || t('editor.beatTitlePlaceholder')}
              </span>
            </button>
          ))}
        </aside>

        <div className="mh-arr-stage">
          <div className="mh-arr-timeline-scroll" ref={viewportRef} data-testid="arr-viewport">
            <div className="mh-arr-canvas" style={{ width: `${canvasWidth}px` }}>
              {/* NLE-style guide: while dragging, a full-height line tracks the
                  active edge (start for a move, end for a resize) with a live
                  time readout — the editing-software affordance. */}
              {drag && (
                <div
                  className="mh-arr-guide"
                  data-testid="arr-drag-guide"
                  aria-hidden="true"
                  style={{
                    left: `${timeToPx(
                      drag.mode === 'move'
                        ? drag.start_sec
                        : drag.start_sec + (drag.duration_sec ?? 0),
                      pxPerSec,
                    )}px`,
                  }}
                >
                  <span className="mh-arr-guide-chip">
                    {formatGuideSec(
                      drag.mode === 'move'
                        ? drag.start_sec
                        : drag.start_sec + (drag.duration_sec ?? 0),
                    )}
                  </span>
                </div>
              )}
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
                  const slot = layout.get(beat.id) ?? { x: 0, lane: 0, width: MIN_CARD_PX };
                  const isDragging = drag?.id === beat.id;
                  // Dragged card follows the pointer's TIME directly (no push);
                  // everyone else sits on the settled layout.
                  const x = isDragging ? timeToPx(p.start_sec ?? 0, pxPerSec) : slot.x;
                  const fullWidth = isDragging
                    ? cardWidthPx(p.duration_sec, pxPerSec)
                    : slot.width;
                  const lane = slot.lane;
                  const durationLabel = formatBeatDuration(p.duration_sec);
                  const width = Math.max(MIN_CARD_PX - CARD_GAP_PX, fullWidth - CARD_GAP_PX);
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
                        left: `${x}px`,
                        width: `${width}px`,
                        height: `${CARD_HEIGHT}px`,
                        top: `${LANES_PAD_TOP + lane * (CARD_HEIGHT + LANE_GAP)}px`,
                      }}
                      onPointerDown={(e) => beginDrag(e, beat, 'move')}
                      onPointerMove={moveDrag}
                      onPointerUp={endDrag}
                      onPointerCancel={endDrag}
                      onClick={() => setSelectedId(beat.id)}
                    >
                      <span
                        className="mh-arr-card-bar"
                        aria-hidden="true"
                        style={beat.color ? { background: beat.color } : undefined}
                      />
                      <div className="mh-arr-card-title">
                        {beat.title || t('editor.beatTitlePlaceholder')}
                      </div>
                      {beat.summary && <div className="mh-arr-card-summary">{beat.summary}</div>}
                      <div className="mh-arr-card-foot">
                        {durationLabel && (
                          <span className="mh-arr-card-chip">{durationLabel}</span>
                        )}
                        <button
                          type="button"
                          className="mh-arr-card-edit"
                          data-testid="arr-card-edit"
                          onPointerDown={(e) => e.stopPropagation()}
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditingId(beat.id);
                          }}
                        >
                          ✎ {t('editor.beatEdit')}
                        </button>
                      </div>
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

              {/* M4 memo-pin rail: time-anchored inspiration memos as draggable
                  pins with floating cards. Owns its own note data + REST. */}
              <MemoRail
                scriptId={scriptId}
                beats={beats}
                pxPerSec={pxPerSec}
                granularity={granularity}
                canvasWidth={canvasWidth}
                majorTickSecs={majorTickSecs}
              />
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
                  className="mh-arr-tray-bar"
                  aria-hidden="true"
                  style={beat.color ? { background: beat.color } : undefined}
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

      {editingBeat && (
        <BeatEditModal
          beat={editingBeat}
          scenes={scenes}
          onSave={(data) => onUpdate(editingBeat.id, data)}
          onClose={() => setEditingId(null)}
          onOpenScene={onOpenScene}
        />
      )}

      {conform && (
        <div
          className="mh-arr-modal-overlay"
          data-testid="beats-conform-modal"
          onClick={(e) => {
            if (e.target === e.currentTarget) setConform(null);
          }}
        >
          <div className="mh-arr-modal" role="dialog" aria-modal="true" aria-label={t('editor.beatConformTitle')}>
            <div className="mh-arr-modal-head">
              <span className="mh-arr-modal-title">{t('editor.beatConformTitle')}</span>
              <button
                type="button"
                className="mh-arr-modal-x"
                aria-label={t('common.cancel')}
                onClick={() => setConform(null)}
              >
                ×
              </button>
            </div>
            <p className="mh-beats-empty-sub">{t('editor.beatConformBody')}</p>
            <div className="mh-arr-modal-foot">
              <button
                type="button"
                className="mh-arr-modal-btn ghost"
                data-testid="beats-conform-resize"
                onClick={() => resolveConform(false)}
              >
                {t('editor.beatConformResizeOnly')}
              </button>
              <button
                type="button"
                className="mh-arr-modal-btn primary"
                data-testid="beats-conform-stretch"
                onClick={() => resolveConform(true)}
              >
                {t('editor.beatConformStretch')}
              </button>
            </div>
          </div>
        </div>
      )}

      {overlays}
    </div>
  );
}
