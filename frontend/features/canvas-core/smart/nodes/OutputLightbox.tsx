// features/canvas-core/smart/nodes/OutputLightbox.tsx
//
// Fullscreen media lightbox (Infinite-Canvas parity G7 — openOutputLightbox):
// multi-image navigation with a counter, natural-resolution readout,
// download / download-all (fetch→blob so cross-origin durable URLs still
// save instead of navigating), a compare divider you drag directly (P1-4,
// with thumbnail source pickers when several upstream inputs qualify), and
// a Regenerate hook. Controlled + presentational — the node owns which item
// is open; regeneration/state lives in smart/regenerate.ts.

import { fullResSrc, mediaSrc } from '../mediaUrl';
import {
  ChevronLeft,
  ChevronRight,
  Download,
  GitCompareArrows,
  RefreshCw,
  X,
  Crop as CropIcon,
  Expand,
  Paintbrush,
  Grid3x3,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { downloadCanvasAssetsZip } from '../../services/canvasGenerationService';
import { lazy, Suspense } from 'react';

import { downloadBlob, downloadName, downloadUrl } from '../downloadMedia';
import { isLikelyPanorama } from './panoramaDetect';

const PanoramaViewer = lazy(() =>
  import('./PanoramaViewer').then((m) => ({ default: m.PanoramaViewer })),
);
import {
  exportFrameTime,
  nextFrameTime,
  type ExportWhich,
} from '../videoFrames';

export interface LightboxItem {
  url: string;
  name?: string;
}

export interface OutputLightboxProps {
  /** IC 导出到画布: when set, exported video frames land here (the caller
   *  drops them onto the canvas as a media node) instead of downloading. */
  onFrameExported?: (blob: Blob, name: string) => void;
  items: LightboxItem[];
  index: number;
  kind: 'image' | 'video';
  onIndexChange: (next: number) => void;
  onClose: () => void;
  /** Meta line addendum (P3-B) — the generating prompt (front truncated),
   *  shown alongside the resolution (Infinite's updatePreviewMetaHint). */
  meta?: string;
  /** Candidate compare underlays (upstream input images, or the newest
   *  archived version as fallback) — enables Compare when non-empty. */
  compareSources?: LightboxItem[];
  onRegenerate?: () => void;
  regenerating?: boolean;
  /** Editing tools (IC ⑧ 图片编辑系统): rendered as a tab bar in the
   *  header; picking one closes the lightbox and opens that editor on the
   *  item on screen, which is handed to the action.
   *  Absent → preview-only lightbox (media/group previews). */
  editActions?: {
    crop?: (item: LightboxItem) => void;
    expand?: (item: LightboxItem) => void;
    mask?: (item: LightboxItem) => void;
    split?: (item: LightboxItem) => void;
  };
}

// downloadUrl / downloadName live in smart/downloadMedia.ts (shared with
// the node toolbar, P2-3) — fetch→blob→anchor for cross-origin safety.

export function OutputLightbox({
  items,
  index,
  kind,
  onIndexChange,
  onClose,
  meta,
  compareSources,
  onRegenerate,
  regenerating,
  editActions,
  onFrameExported,
}: OutputLightboxProps) {
  const current = items[index];
  const pickTool = (fn?: (item: LightboxItem) => void) => () => {
    if (!fn || !current) return;
    onClose();
    fn(current);
  };
  const [resolution, setResolution] = useState<string>('');
  const [compareOn, setCompareOn] = useState(false);
  // IC 360 panorama: offered for keyword names or ~2:1 equirect images;
  // mutually exclusive with compare (both own the stage).
  const [panoramaOn, setPanoramaOn] = useState(false);
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null);
  useEffect(() => {
    setPanoramaOn(false);
    setNaturalSize(null);
  }, [index]);
  const [sliderPct, setSliderPct] = useState(50);
  const [compareIndex, setCompareIndex] = useState(0);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const sources = compareSources ?? [];
  const compareUrl = sources[Math.min(compareIndex, sources.length - 1)]?.url ?? null;

  // ── Wheel zoom + drag pan (P0-5, Infinite parity) ─────────────────────────
  // Cursor-anchored wheel zoom over the stage; drag pans; double-click and
  // item switches reset. Disabled in compare mode (the divider math needs a
  // stable box) and for video (controls own the wheel there some day).
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const stageRef = useRef<HTMLDivElement | null>(null);
  const panDrag = useRef<{ startX: number; startY: number; baseX: number; baseY: number } | null>(null);
  const zoomEnabled = kind === 'image' && !compareOn;

  // ── Video frame stepping + export (P2-8, Infinite parity) ─────────────────
  // No autoplay (Infinite): the wheel and arrow keys pause-and-seek by
  // fixed 30fps frames; the toolbar exports first/current/last frames as
  // PNGs via a canvas draw. The <video> is same-origin under prod's Vercel
  // rewrite, so canvas export won't taint; a cross-origin source throws
  // SecurityError → surfaced as an export error.
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const stepVideoFrame = useCallback((dir: 1 | -1) => {
    const v = videoRef.current;
    if (!v) return;
    v.pause();
    v.currentTime = nextFrameTime(v.currentTime, dir, v.duration);
  }, []);

  const exportFrame = useCallback(
    (which: ExportWhich) => {
      const v = videoRef.current;
      if (!v) return;
      setExportError(null);
      const w = v.videoWidth;
      const h = v.videoHeight;
      if (!w || !h) {
        setExportError('Frame not ready');
        return;
      }
      v.pause();
      const target = exportFrameTime(which, v.currentTime, v.duration);
      const original = v.currentTime;
      let done = false;
      const draw = () => {
        if (done) return;
        done = true;
        v.removeEventListener('seeked', draw);
        try {
          const canvas = document.createElement('canvas');
          canvas.width = w;
          canvas.height = h;
          const ctx = canvas.getContext('2d');
          if (!ctx) throw new Error('no 2d context');
          ctx.drawImage(v, 0, 0, w, h);
          canvas.toBlob((blob) => {
            if (blob) {
              const base = (current?.name ?? 'frame').replace(/\.[^./]+$/, '');
              const name = `${base}-${which}-frame.png`;
              if (onFrameExported) onFrameExported(blob, name);
              else downloadBlob(blob, name);
            } else {
              setExportError('Export failed');
            }
            // Restore the viewer's position for current/last exports.
            if (which !== 'current') v.currentTime = original;
          }, 'image/png');
        } catch (err) {
          console.error('frame export failed', err);
          setExportError('Export failed (cross-origin?)');
        }
      };
      v.addEventListener('seeked', draw);
      v.currentTime = target;
      // Belt-and-braces: if the browser was already at the target, `seeked`
      // may not fire — draw on the next tick.
      if (Math.abs(v.currentTime - target) < 1e-3) draw();
    },
    [current?.name],
  );
  // Progressive load (P1-3, adapted): no thumbnail variants exist on the
  // durable endpoints, so "progressive" here means an immediate shimmer
  // skeleton instead of a white void, plus a broken-image recovery state
  // (Infinite: fast thumb → swap; ours: skeleton → swap).
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [retryNonce, setRetryNonce] = useState(0);
  const zoomActive = zoom !== 1 || pan.x !== 0 || pan.y !== 0;

  const resetZoom = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  // A different item (or compare toggle) always starts at fit.
  useEffect(() => {
    resetZoom();
  }, [index, compareOn, resetZoom]);

  // New media → back to the skeleton until its own load settles.
  useEffect(() => {
    setLoadState('loading');
  }, [current?.url, retryNonce]);

  const applyWheelZoom = useCallback(
    (e: WheelEvent) => {
      // Video: the wheel scrubs frames instead of zooming (P2-8).
      if (kind === 'video') {
        e.preventDefault();
        stepVideoFrame(e.deltaY < 0 ? -1 : 1);
        return;
      }
      if (!zoomEnabled) return;
      e.preventDefault();
      const stage = stageRef.current;
      if (!stage) return;
      const rect = stage.getBoundingClientRect();
      // Cursor relative to the stage centre (the flex-centered origin).
      const cx = e.clientX - (rect.left + rect.width / 2);
      const cy = e.clientY - (rect.top + rect.height / 2);
      setZoom((prevZoom) => {
        const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        // Same envelope as Infinite: floor 0.05, generous ceiling.
        const nextZoom = Math.min(32, Math.max(0.05, prevZoom * factor));
        setPan((prevPan) => {
          // Keep the content point under the cursor stationary.
          const px = (cx - prevPan.x) / prevZoom;
          const py = (cy - prevPan.y) / prevZoom;
          return { x: cx - px * nextZoom, y: cy - py * nextZoom };
        });
        return nextZoom;
      });
    },
    [zoomEnabled, kind, stepVideoFrame],
  );

  // React marks onWheel passive on some roots — bind non-passive by hand so
  // preventDefault reliably stops page scroll while zooming.
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;
    stage.addEventListener('wheel', applyWheelZoom, { passive: false });
    return () => stage.removeEventListener('wheel', applyWheelZoom);
  }, [applyWheelZoom]);

  const onPanStart = useCallback(
    (e: React.MouseEvent) => {
      if (!zoomEnabled || e.button !== 0) return;
      e.preventDefault();
      panDrag.current = { startX: e.clientX, startY: e.clientY, baseX: pan.x, baseY: pan.y };
      const onMove = (ev: MouseEvent) => {
        const d = panDrag.current;
        if (!d) return;
        setPan({ x: d.baseX + (ev.clientX - d.startX), y: d.baseY + (ev.clientY - d.startY) });
      };
      const onUp = () => {
        panDrag.current = null;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [zoomEnabled, pan.x, pan.y],
  );

  // ── Compare divider drag (P1-4, Infinite parity) ──────────────────────────
  // The split line itself is the drag target (30px hit zone + grip knob):
  // pointer capture keeps the drag alive when the cursor outruns the strip,
  // matching Infinite's previewCompareHandle (smart-canvas.js:16596).
  const compareStageRef = useRef<HTMLDivElement | null>(null);
  const compareDrag = useRef(false);

  const setDividerFromClientX = useCallback((clientX: number) => {
    const stage = compareStageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    const pct = Math.max(
      0,
      Math.min(100, ((clientX - rect.left) / Math.max(1, rect.width)) * 100),
    );
    setSliderPct(pct);
  }, []);

  const onDividerPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      // jsdom has no pointer capture — optional-call keeps tests honest.
      e.currentTarget.setPointerCapture?.(e.pointerId);
      compareDrag.current = true;
      setDividerFromClientX(e.clientX);
    },
    [setDividerFromClientX],
  );

  const onDividerPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!compareDrag.current) return;
      e.preventDefault();
      e.stopPropagation();
      setDividerFromClientX(e.clientX);
    },
    [setDividerFromClientX],
  );

  const onDividerPointerEnd = useCallback((e: React.PointerEvent) => {
    compareDrag.current = false;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  }, []);

  const onDividerKeyDown = useCallback((e: React.KeyboardEvent) => {
    // Keep arrow keys from switching lightbox images while adjusting.
    e.stopPropagation();
    if (e.key === 'ArrowLeft') setSliderPct((p) => Math.max(0, p - 2));
    else if (e.key === 'ArrowRight') setSliderPct((p) => Math.min(100, p + 2));
  }, []);

  const goto = useCallback(
    (next: number) => {
      if (items.length < 2) return;
      const wrapped = (next + items.length) % items.length;
      setResolution('');
      onIndexChange(wrapped);
    },
    [items.length, onIndexChange],
  );

  // Focus the dialog on mount so arrow keys / Escape work immediately.
  useEffect(() => {
    rootRef.current?.focus();
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      // Video: arrows scrub frames instead of switching items (P2-8).
      if (kind === 'video') {
        if (e.key === 'ArrowRight') stepVideoFrame(1);
        else if (e.key === 'ArrowLeft') stepVideoFrame(-1);
        return;
      }
      if (e.key === 'ArrowRight') goto(index + 1);
      else if (e.key === 'ArrowLeft') goto(index - 1);
    },
    [onClose, goto, index, kind, stepVideoFrame],
  );

  /** Per-file loop — the single-download path and the zip fallback. */
  const downloadEach = useCallback(
    async (targets: ReadonlyArray<readonly [LightboxItem, number]>) => {
      for (const [item, i] of targets) {
        try {
          await downloadUrl(item, i);
        } catch (err) {
          setDownloadError(err instanceof Error ? err.message : 'Download failed');
          return;
        }
      }
    },
    [],
  );

  const doDownload = useCallback(
    (only?: LightboxItem, onlyIndex?: number) => {
      setDownloadError(null);
      if (only) {
        void downloadEach([[only, onlyIndex ?? 0] as const]);
        return;
      }
      // Download All → one server-side zip (P2-7); fall back to the per-file
      // loop if the endpoint errors (deploy-skew window: frontend ships
      // seconds ahead of the backend).
      void (async () => {
        try {
          const filename = `${downloadName(items[0], 0).replace(/\.[^./]+$/, '') || 'canvas'}-assets.zip`;
          const blob = await downloadCanvasAssetsZip(
            filename,
            items.map((it, i) => ({ url: it.url, name: downloadName(it, i) })),
          );
          downloadBlob(blob, filename);
        } catch {
          await downloadEach(items.map((it, i) => [it, i] as const));
        }
      })();
    },
    [items, downloadEach],
  );

  if (!current) return null;

  // Portal to <body>: this component renders inside an RF node subtree whose
  // ancestors carry CSS transforms — a transform makes the ancestor the
  // containing block for position:fixed, collapsing "fullscreen" to a small
  // box inside the node (adversarial-review CRITICAL, screenshot-proven).
  return createPortal(
    <div
      ref={rootRef}
      data-testid="output-lightbox"
      role="dialog"
      aria-label="Output preview"
      tabIndex={-1}
      onKeyDown={onKeyDown}
      onClick={onClose}
      className="mh-lightbox-backdrop nodrag nopan nowheel fixed inset-0 z-[70] flex flex-col outline-none"
    >
      {/* Toolbar */}
      <div
        className="flex items-center justify-between px-4 py-2 text-canvas-text"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 text-xs">
          {items.length > 1 && (
            <span data-testid="lightbox-counter">{`${index + 1} / ${items.length}`}</span>
          )}
          {resolution && <span data-testid="lightbox-resolution">{resolution}</span>}
          {meta && (
            <span
              data-testid="lightbox-meta"
              className="max-w-[40vw] truncate text-canvas-muted"
              title={meta}
            >
              {meta.length > 60 ? `${meta.slice(0, 60)}…` : meta}
            </span>
          )}
          {(downloadError || exportError) && (
            <span role="alert" className="text-rose-400">
              {downloadError || exportError}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {kind === 'video' && (
            // Frame export (P2-8): grab first / current / last as PNGs.
            <>
              <LightboxButton label="First Frame" onClick={() => exportFrame('first')}>
                <span className="text-[11px]">First</span>
              </LightboxButton>
              <LightboxButton
                label="Current Frame"
                onClick={() => exportFrame('current')}
              >
                <span className="text-[11px]">Frame</span>
              </LightboxButton>
              <LightboxButton label="Last Frame" onClick={() => exportFrame('last')}>
                <span className="text-[11px]">Last</span>
              </LightboxButton>
            </>
          )}
          {kind === 'image' &&
            isLikelyPanorama(current?.name ?? '', naturalSize?.w, naturalSize?.h) && (
              <button
                type="button"
                data-testid="lightbox-panorama-toggle"
                aria-pressed={panoramaOn}
                onClick={() => {
                  setPanoramaOn((v) => !v);
                  setCompareOn(false);
                }}
                className={`rounded-lg px-2 py-1 text-[11px] font-semibold ${
                  panoramaOn
                    ? 'bg-canvas-strong text-canvas-card'
                    : 'text-canvas-muted hover:text-canvas-text'
                }`}
              >
                360°
              </button>
            )}
          {compareUrl && kind === 'image' && (
            <LightboxButton
              label="Compare"
              active={compareOn}
              onClick={() => setCompareOn((v) => !v)}
            >
              <GitCompareArrows size={14} />
            </LightboxButton>
          )}
          {editActions && (
            <div
              data-testid="lightbox-edit-bar"
              className="flex items-center gap-1"
            >
              {editActions.crop && (
                <LightboxButton label="Crop" onClick={pickTool(editActions.crop)}>
                  <CropIcon size={14} />
                </LightboxButton>
              )}
              {editActions.expand && (
                <LightboxButton label="Expand" onClick={pickTool(editActions.expand)}>
                  <Expand size={14} />
                </LightboxButton>
              )}
              {editActions.mask && (
                <LightboxButton label="Mask" onClick={pickTool(editActions.mask)}>
                  <Paintbrush size={14} />
                </LightboxButton>
              )}
              {editActions.split && (
                <LightboxButton label="Split" onClick={pickTool(editActions.split)}>
                  <Grid3x3 size={14} />
                </LightboxButton>
              )}
            </div>
          )}
          {onRegenerate && (
            <LightboxButton
              label="Regenerate"
              onClick={onRegenerate}
              disabled={regenerating}
            >
              <RefreshCw size={14} className={regenerating ? 'animate-spin' : undefined} />
            </LightboxButton>
          )}
          <LightboxButton label="Download" onClick={() => doDownload(current, index)}>
            <Download size={14} />
          </LightboxButton>
          {items.length > 1 && (
            <LightboxButton label="Download All" onClick={() => doDownload()}>
              <Download size={14} />
              <span className="text-[11px]">All</span>
            </LightboxButton>
          )}
          <LightboxButton label="Close" onClick={onClose}>
            <X size={14} />
          </LightboxButton>
        </div>
      </div>

      {/* Stage */}
      <div
        ref={stageRef}
        data-testid="lightbox-stage"
        className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden px-14 pb-6"
      >
        {items.length > 1 && (
          <button
            type="button"
            aria-label="Previous"
            onClick={(e) => {
              e.stopPropagation();
              goto(index - 1);
            }}
            className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full border border-canvas-line bg-canvas-card/70 p-2 text-canvas-text hover:bg-canvas-card"
          >
            <ChevronLeft size={18} />
          </button>
        )}
        <div
          data-testid="lightbox-zoom-layer"
          className={`relative max-h-full max-w-full ${
            zoomEnabled ? (panDrag.current ? 'cursor-grabbing' : 'cursor-grab') : ''
          }`}
          style={
            zoomActive
              ? { transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }
              : undefined
          }
          onClick={(e) => e.stopPropagation()}
          onMouseDown={onPanStart}
          onDoubleClick={zoomEnabled ? resetZoom : undefined}
        >
          {kind === 'video' ? (
            <video
              ref={videoRef}
              data-testid="lightbox-video"
              src={fullResSrc(current.url)}
              controls
              // No autoplay (Infinite parity): frame scrubbing and playback
              // are mutually exclusive; the node's inline <video> already
              // covers "glance at it". crossOrigin lets canvas export the
              // same-origin stream without tainting.
              crossOrigin="anonymous"
              className="max-h-[80vh] max-w-full"
              onLoadedMetadata={(e) => {
                const v = e.currentTarget;
                if (v.videoWidth) setResolution(`${v.videoWidth} × ${v.videoHeight}`);
              }}
            />
          ) : panoramaOn ? (
            <div className="h-[80vh] w-[min(90vw,1400px)]">
              <Suspense
                fallback={
                  <div className="flex h-full items-center justify-center text-xs text-canvas-muted">
                    Loading 360° viewer…
                  </div>
                }
              >
                <PanoramaViewer
                  src={fullResSrc(current.url)}
                  exportName={`${(current?.name ?? 'panorama').replace(/\.[^./]+$/, '')}-view.png`}
                  onExport={
                    onFrameExported
                      ? (blob, name) => onFrameExported(blob, name)
                      : undefined
                  }
                />
              </Suspense>
            </div>
          ) : compareOn && compareUrl ? (
            <div
              ref={compareStageRef}
              className="relative select-none"
              data-testid="compare-stage"
            >
              {/* The CURRENT image defines the box (no letterbox), so the
                  divider always aligns with its real pixels; the compare
                  source sits underneath, letterboxed if aspects differ. */}
              <img
                data-testid="compare-original"
                src={fullResSrc(compareUrl)}
                alt="Compare source"
                draggable={false}
                className="absolute inset-0 block h-full w-full object-contain"
              />
              <img
                data-testid="compare-result"
                src={fullResSrc(current.url)}
                alt="Current version"
                draggable={false}
                className="relative block max-h-[80vh] max-w-full"
                style={{ clipPath: `inset(0 ${100 - sliderPct}% 0 0)` }}
              />
              {/* The divider itself is the drag handle: 30px hit strip with
                  a hairline + grip knob (Infinite .preview-compare-handle). */}
              <div
                data-testid="compare-divider"
                role="slider"
                aria-label="Compare position"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(sliderPct)}
                tabIndex={0}
                style={{ left: `${sliderPct}%` }}
                className="absolute inset-y-0 z-[6] -ml-[15px] w-[30px] cursor-ew-resize touch-none outline-none"
                onPointerDown={onDividerPointerDown}
                onPointerMove={onDividerPointerMove}
                onPointerUp={onDividerPointerEnd}
                onPointerCancel={onDividerPointerEnd}
                onKeyDown={onDividerKeyDown}
              >
                <div
                  aria-hidden
                  className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-white/90 shadow-[0_0_0_1px_rgba(15,23,42,0.22)]"
                />
                <div
                  aria-hidden
                  className="absolute left-1/2 top-1/2 h-[30px] w-[30px] -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/75 bg-slate-900/70 shadow-[0_8px_24px_rgba(15,23,42,0.24)]"
                />
              </div>
              {sources.length > 1 && (
                <div
                  data-testid="compare-thumbs"
                  className="absolute bottom-2 right-2 z-[7] flex max-w-[70%] items-center gap-1.5 overflow-x-auto"
                  onClick={(e) => e.stopPropagation()}
                >
                  {sources.map((s, i) => (
                    <button
                      key={s.url}
                      type="button"
                      data-testid={`compare-thumb-${i}`}
                      title={s.name || `Source ${i + 1}`}
                      aria-pressed={i === compareIndex}
                      onClick={() => setCompareIndex(i)}
                      className={`h-9 w-9 shrink-0 overflow-hidden rounded-[10px] border transition-colors ${
                        i === compareIndex
                          ? 'border-canvas-strong'
                          : 'border-canvas-line hover:border-canvas-text'
                      }`}
                    >
                      {/* 36px picker — preview tier on purpose; only the
                          two large compare panes take the original. */}
                      <img
                        src={mediaSrc(s.url)}
                        alt={s.name || `Source ${i + 1}`}
                        draggable={false}
                        className="h-full w-full object-cover"
                      />
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <>
              {loadState === 'loading' && (
                <div
                  data-testid="lightbox-skeleton"
                  aria-label="Loading image"
                  className="mh-loading-cell h-[min(60vh,60vw)] w-[min(60vh,60vw)] rounded-lg"
                />
              )}
              {loadState === 'error' ? (
                <div
                  data-testid="lightbox-load-error"
                  className="flex h-[min(40vh,40vw)] w-[min(60vh,60vw)] flex-col items-center justify-center gap-3 rounded-lg border border-canvas-line bg-canvas-card/60 text-canvas-muted"
                >
                  <span className="text-sm">Failed to load image</span>
                  <button
                    type="button"
                    className="rounded-full border border-canvas-line px-3 py-1 text-xs text-canvas-text hover:bg-canvas-card"
                    onClick={() => setRetryNonce((n) => n + 1)}
                  >
                    Retry
                  </button>
                </div>
              ) : (
                <img
                  key={`${current.url}#${retryNonce}`}
                  data-testid="lightbox-image"
                  src={fullResSrc(current.url)}
                  alt={current.name || 'Output'}
                  draggable={false}
                  className={`block max-h-[80vh] max-w-full object-contain ${
                    loadState === 'ready' ? '' : 'hidden'
                  }`}
                  onLoad={(e) => {
                    setLoadState('ready');
                    const img = e.currentTarget;
                    if (img.naturalWidth) {
                      setResolution(`${img.naturalWidth} × ${img.naturalHeight}`);
                      setNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
                    }
                  }}
                  onError={() => setLoadState('error')}
                />
              )}
            </>
          )}
        </div>
        {items.length > 1 && (
          <button
            type="button"
            aria-label="Next"
            onClick={(e) => {
              e.stopPropagation();
              goto(index + 1);
            }}
            className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full border border-canvas-line bg-canvas-card/70 p-2 text-canvas-text hover:bg-canvas-card"
          >
            <ChevronRight size={18} />
          </button>
        )}
        {zoomActive && (
          <span
            data-testid="lightbox-zoom-readout"
            className="absolute bottom-3 left-4 rounded border border-canvas-line bg-canvas-card/70 px-2 py-0.5 text-[11px] tabular-nums text-canvas-text"
            onClick={(e) => e.stopPropagation()}
            title="Double-click the image to reset"
          >
            {`${Math.round(zoom * 100)}%`}
          </span>
        )}
      </div>
    </div>,
    document.body,
  );
}

function LightboxButton({
  label,
  onClick,
  children,
  active,
  disabled,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  active?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-1 rounded border border-canvas-line px-2 py-1.5 text-canvas-text hover:bg-canvas-card disabled:cursor-not-allowed disabled:opacity-50 ${
        active ? 'bg-canvas-card' : 'bg-canvas-card/50'
      }`}
    >
      {children}
    </button>
  );
}
