// features/canvas-core/smart/nodes/OutputLightbox.tsx
//
// Fullscreen media lightbox (Infinite-Canvas parity G7 — openOutputLightbox):
// multi-image navigation with a counter, natural-resolution readout,
// download / download-all (fetch→blob so cross-origin durable URLs still
// save instead of navigating), a previous-version compare slider, and a
// Regenerate hook. Controlled + presentational — the node owns which item
// is open; regeneration/state lives in smart/regenerate.ts.

import {
  ChevronLeft,
  ChevronRight,
  Download,
  GitCompareArrows,
  RefreshCw,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export interface LightboxItem {
  url: string;
  name?: string;
}

export interface OutputLightboxProps {
  items: LightboxItem[];
  index: number;
  kind: 'image' | 'video';
  onIndexChange: (next: number) => void;
  onClose: () => void;
  /** Previous-version URL — enables the Compare slider when present. */
  compareUrl?: string | null;
  onRegenerate?: () => void;
  regenerating?: boolean;
}

function downloadName(item: LightboxItem, fallbackIndex: number): string {
  if (item.name) return item.name;
  const tail = item.url.split('/').filter(Boolean).pop() ?? '';
  return tail || `output-${fallbackIndex + 1}`;
}

/** fetch→blob→anchor: the `download` attribute is ignored on cross-origin
 *  URLs (the API host differs from the app origin), so a plain anchor would
 *  navigate away instead of saving. */
async function downloadUrl(item: LightboxItem, fallbackIndex: number): Promise<void> {
  const res = await fetch(item.url);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = href;
    link.download = downloadName(item, fallbackIndex);
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(href);
  }
}

export function OutputLightbox({
  items,
  index,
  kind,
  onIndexChange,
  onClose,
  compareUrl,
  onRegenerate,
  regenerating,
}: OutputLightboxProps) {
  const current = items[index];
  const [resolution, setResolution] = useState<string>('');
  const [compareOn, setCompareOn] = useState(false);
  const [sliderPct, setSliderPct] = useState(50);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // ── Wheel zoom + drag pan (P0-5, Infinite parity) ─────────────────────────
  // Cursor-anchored wheel zoom over the stage; drag pans; double-click and
  // item switches reset. Disabled in compare mode (the divider math needs a
  // stable box) and for video (controls own the wheel there some day).
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const stageRef = useRef<HTMLDivElement | null>(null);
  const panDrag = useRef<{ startX: number; startY: number; baseX: number; baseY: number } | null>(null);
  const zoomEnabled = kind === 'image' && !compareOn;
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
    [zoomEnabled],
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
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowRight') goto(index + 1);
      else if (e.key === 'ArrowLeft') goto(index - 1);
    },
    [onClose, goto, index],
  );

  const doDownload = useCallback(
    (only?: LightboxItem, onlyIndex?: number) => {
      setDownloadError(null);
      const targets = only ? [[only, onlyIndex ?? 0] as const] : items.map((it, i) => [it, i] as const);
      void (async () => {
        for (const [item, i] of targets) {
          try {
            await downloadUrl(item, i);
          } catch (err) {
            setDownloadError(err instanceof Error ? err.message : 'Download failed');
            return;
          }
        }
      })();
    },
    [items],
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
      className="nodrag nopan nowheel fixed inset-0 z-[70] flex flex-col bg-black/85 outline-none"
    >
      {/* Toolbar */}
      <div
        className="flex items-center justify-between px-4 py-2 text-slate-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 text-xs">
          {items.length > 1 && (
            <span data-testid="lightbox-counter">{`${index + 1} / ${items.length}`}</span>
          )}
          {resolution && <span data-testid="lightbox-resolution">{resolution}</span>}
          {downloadError && (
            <span role="alert" className="text-rose-400">
              {downloadError}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {compareUrl && kind === 'image' && (
            <LightboxButton
              label="Compare"
              active={compareOn}
              onClick={() => setCompareOn((v) => !v)}
            >
              <GitCompareArrows size={14} />
            </LightboxButton>
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
            className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2 text-slate-200 hover:bg-white/20"
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
              data-testid="lightbox-video"
              src={current.url}
              controls
              autoPlay
              className="max-h-[80vh] max-w-full"
              onLoadedMetadata={(e) => {
                const v = e.currentTarget;
                if (v.videoWidth) setResolution(`${v.videoWidth} × ${v.videoHeight}`);
              }}
            />
          ) : compareOn && compareUrl ? (
            <div className="relative select-none" data-testid="compare-stage">
              {/* The CURRENT image defines the box (no letterbox), so the
                  divider always aligns with its real pixels; the previous
                  version sits underneath, letterboxed if aspects differ. */}
              <img
                data-testid="compare-original"
                src={compareUrl}
                alt="Previous version"
                draggable={false}
                className="absolute inset-0 block h-full w-full object-contain"
              />
              <img
                data-testid="compare-result"
                src={current.url}
                alt="Current version"
                draggable={false}
                className="relative block max-h-[80vh] max-w-full"
                style={{ clipPath: `inset(0 ${100 - sliderPct}% 0 0)` }}
              />
              <div
                aria-hidden
                className="pointer-events-none absolute inset-y-0 w-0.5 bg-white/80"
                style={{ left: `${sliderPct}%` }}
              />
              <input
                type="range"
                min={0}
                max={100}
                value={sliderPct}
                aria-label="Compare position"
                onChange={(e) => setSliderPct(Number(e.target.value))}
                onKeyDown={(e) => e.stopPropagation()}
                className="absolute inset-x-0 bottom-2 mx-auto w-2/3 cursor-ew-resize"
              />
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
                  src={current.url}
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
            className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2 text-slate-200 hover:bg-white/20"
          >
            <ChevronRight size={18} />
          </button>
        )}
        {zoomActive && (
          <span
            data-testid="lightbox-zoom-readout"
            className="absolute bottom-3 left-4 rounded bg-white/10 px-2 py-0.5 text-[11px] tabular-nums text-slate-200"
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
      className={`flex items-center gap-1 rounded px-2 py-1.5 text-slate-200 hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-50 ${
        active ? 'bg-white/20' : 'bg-white/5'
      }`}
    >
      {children}
    </button>
  );
}
