import React, { useState, useRef, useCallback } from 'react';
import { Star, FileText, Sparkles, Eye, Clock, Timer } from 'lucide-react';

/**
 * Shared presentational kit for media/resource detail panels.
 *
 * Single source of truth for the detail-card look used by BOTH the download
 * detail (MediaCard / VideoDetailPanel) and the resource-library detail
 * (ResourceDetailPage). Each module fills in its own content; the styling
 * lives here so a visual change touches one file, not many.
 *
 * Class strings are extracted verbatim from the original MediaCard markup so
 * existing views render byte-identically after adopting the kit.
 */

// ── Resizable side panel ─────────────────────────────────────────────────────
export const MIN_PANEL_WIDTH = 380;
export const MAX_PANEL_WIDTH = 800;
export const DEFAULT_PANEL_WIDTH = 420;

const PANEL_WIDTH_STORAGE_PREFIX = 'nous.panelWidth.';

/**
 * Read a persisted panel width for `key` (see `nous.panelWidth.<name>`
 * convention), clamped to [min, max] to defend against a stale/garbage value
 * (e.g. a range change across a deploy). Falls back to `initial` when unset,
 * unparsable, or when storage is unavailable (SSR / privacy mode) — the
 * try/catch keeps that last case a silent no-op rather than a thrown error.
 */
export function loadPanelWidth(
  key: string,
  initial: number,
  min: number = MIN_PANEL_WIDTH,
  max: number = MAX_PANEL_WIDTH,
): number {
  try {
    const raw = window.localStorage.getItem(PANEL_WIDTH_STORAGE_PREFIX + key);
    if (raw === null) return initial;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return initial;
    return Math.min(max, Math.max(min, parsed));
  } catch {
    return initial;
  }
}

/** Persist a panel width for `key`. Silently no-ops when storage is unavailable. */
export function savePanelWidth(key: string, width: number): void {
  try {
    window.localStorage.setItem(PANEL_WIDTH_STORAGE_PREFIX + key, String(width));
  } catch {
    // SSR / privacy mode — fall back to in-memory-only state.
  }
}

/**
 * Drag-to-resize state for a right-hand detail panel.
 *
 * Pass `storageKey` to persist the user's last dragged width across sessions
 * (`nous.panelWidth.<storageKey>`). The stored value is read once on init and
 * written once per drag, on mouseup — never during mousemove, to avoid
 * hammering localStorage on every pixel of movement. Omitting `storageKey`
 * keeps the hook's behavior exactly as before (in-memory width only).
 */
export function useResizablePanel(initial: number = DEFAULT_PANEL_WIDTH, storageKey?: string) {
  const [panelWidth, setPanelWidth] = useState(() =>
    storageKey ? loadPanelWidth(storageKey, initial) : initial,
  );
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(initial);
  const latestWidth = useRef(panelWidth);
  latestWidth.current = panelWidth;

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragStartX.current = e.clientX;
    dragStartWidth.current = panelWidth;
    const onMove = (ev: MouseEvent) => {
      // Panel sits on the RIGHT, so dragging left (smaller clientX) widens it.
      const delta = dragStartX.current - ev.clientX;
      const newWidth = Math.min(MAX_PANEL_WIDTH, Math.max(MIN_PANEL_WIDTH, dragStartWidth.current + delta));
      latestWidth.current = newWidth;
      setPanelWidth(newWidth);
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (storageKey) savePanelWidth(storageKey, latestWidth.current);
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [panelWidth, storageKey]);

  return { panelWidth, handleResizeStart, setPanelWidth };
}

/** The draggable divider between main content and the right panel. */
export function ResizeHandle({ onMouseDown }: { onMouseDown: (e: React.MouseEvent) => void }) {
  return (
    <div
      onMouseDown={onMouseDown}
      className="hidden md:block w-1 shrink-0 cursor-col-resize group relative mx-1.5"
    >
      <div className="absolute inset-y-0 -left-1 -right-1 group-hover:bg-blue-500/30 transition-colors rounded" />
    </div>
  );
}

// ── Card shell ───────────────────────────────────────────────────────────────
/** The big rounded card that wraps detail content. */
export const detailCardClass =
  'bg-ink-900 border border-ink-800 rounded-2xl overflow-hidden hover:border-ink-700 transition-all duration-300 shadow-lg flex flex-col max-w-full';

export function DetailCard({ className = '', children }: { className?: string; children: React.ReactNode }) {
  return <div className={`${detailCardClass} ${className}`}>{children}</div>;
}

// ── Badges ───────────────────────────────────────────────────────────────────
export function DetailBadge({
  children,
  variant = 'neutral',
}: {
  children: React.ReactNode;
  variant?: 'neutral' | 'accent';
}) {
  if (variant === 'accent') {
    return (
      <span className="px-2 py-1 text-xs font-semibold bg-[var(--accent-soft)] text-[var(--accent-text)] rounded-md border border-[var(--accent-border)]">
        {children}
      </span>
    );
  }
  return (
    <span className="px-2 py-1 text-xs font-semibold bg-ink-800 text-ink-300 rounded-md border border-ink-700 uppercase tracking-wider">
      {children}
    </span>
  );
}

// ── Release time + duration meta row ─────────────────────────────────────────
/**
 * The "Release Time · Duration" row with clock/timer icons. Pass pre-formatted
 * strings. `durationSuffix` renders after the duration value (e.g. a mobile-only
 * resolution badge in the download view).
 */
export function MetaTimeRow({
  releaseTime,
  duration,
  durationSuffix,
  className = '',
}: {
  releaseTime?: string | null;
  duration?: string | null;
  durationSuffix?: React.ReactNode;
  className?: string;
}) {
  if (!releaseTime && !duration) return null;
  // `sm:flex-wrap` + per-item `min-w-0`: on sm+ this is a row, and the two
  // items together ("Release Time: <full timestamp>" + "Duration: …") are
  // ~360px wide — wider than the download detail's info island at its 250–420px
  // widths. Without wrapping they overflowed and the Duration text was clipped
  // by the panel's scroll container. `min-w-0` additionally lets a single item
  // wrap its own text at the narrowest widths instead of overflowing alone.
  return (
    <div className={`flex flex-col sm:flex-row sm:flex-wrap sm:items-center gap-y-2 gap-x-6 text-sm text-ink-400 ${className}`}>
      {releaseTime && (
        <div className="flex items-center gap-2 min-w-0">
          <Clock size={14} className="text-ink-500 shrink-0" />
          <span>Release Time: <span className="text-ink-300 font-medium">{releaseTime}</span></span>
        </div>
      )}
      {duration && (
        <div className="flex items-center gap-2 min-w-0">
          <Timer size={14} className="text-ink-500 shrink-0" />
          <span>Duration: <span className="text-ink-300 font-medium">{duration}</span></span>
          {durationSuffix}
        </div>
      )}
    </div>
  );
}

// ── Stat grid (the signature mini-card row) ──────────────────────────────────
/**
 * `adaptive` opts the grid into a container query: below 23rem of available
 * container width it drops to two columns. Callers that pass it must sit
 * inside an `@container` ancestor — MediaCard's bare/island info column is one.
 *
 * 23rem (368px) is where four cards stop fitting their labels: a card needs
 * ~78px (the "COMMENTS" label at 10px uppercase plus `p-3`), so four of them
 * plus three 16px gaps need ~360px. Note the query measures the container's
 * CONTENT box, so this is the space the grid actually gets, with the host's
 * padding already subtracted.
 *
 * The viewport `sm:` breakpoints elsewhere in this kit can't express that —
 * the download detail's info island is drag-resizable from 250px to 640px
 * while the viewport stays desktop-wide, so viewport breakpoints read "large"
 * exactly when the panel is at its narrowest.
 *
 * Class strings are written out in full (not composed from fragments) because
 * Tailwind's scanner only sees literal strings in the source.
 */
export function StatGrid({
  cols,
  adaptive = false,
  className = '',
  children,
}: {
  cols: 3 | 4;
  adaptive?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const colsClass = adaptive
    ? cols === 3
      ? 'grid-cols-2 @min-[23rem]:grid-cols-3'
      : 'grid-cols-2 @min-[23rem]:grid-cols-4'
    : cols === 3
      ? 'grid-cols-3'
      : 'grid-cols-4';
  return (
    <div className={`grid ${colsClass} gap-2 sm:gap-4 ${className}`}>
      {children}
    </div>
  );
}

const STAT_CARD_BASE =
  'flex flex-col items-center justify-center p-2 sm:p-3 bg-ink-950 rounded-xl border border-ink-800';

export function StatCard({
  icon,
  value,
  label,
  onClick,
  title,
}: {
  icon: React.ReactNode;
  value: React.ReactNode;
  label: React.ReactNode;
  onClick?: () => void;
  title?: string;
}) {
  const inner = (
    <>
      {icon}
      <span className="max-w-full truncate text-xs sm:text-sm font-bold text-ink-50">{value}</span>
      {/* `max-w-full truncate`: the label is the widest thing in the card
          ("COMMENTS" / "COLLECTS"), and without a cap it bled past the card
          edge — the visible symptom being a 4th stat card that looked sliced
          in half at narrow panel widths. */}
      <span className="max-w-full truncate text-[9px] sm:text-[10px] text-ink-500 uppercase tracking-wider mt-0.5">{label}</span>
    </>
  );
  if (onClick) {
    return (
      <button
        onClick={onClick}
        title={title}
        className={`${STAT_CARD_BASE} hover:border-emerald-500/50 hover:bg-emerald-500/5 transition-all cursor-pointer group`}
      >
        {inner}
      </button>
    );
  }
  return <div className={STAT_CARD_BASE}>{inner}</div>;
}

// ── Section label ────────────────────────────────────────────────────────────
export function SectionLabel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <h4 className={`text-[11px] font-semibold text-ink-500 uppercase tracking-widest ${className}`}>
      {children}
    </h4>
  );
}

// ── AI intent badges (transcript / summary / analyze status chips) ───────────
/** Lifecycle colour for an AI status pill. */
export function aiIntentPillClass(status?: string): string {
  switch (status) {
    case 'completed':
      return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40';
    case 'processing':
    case 'pending':
    case 'running':
      return 'bg-amber-500/15 text-warn border-amber-500/40 animate-pulse';
    case 'failed':
      return 'bg-red-500/15 text-red-300 border-red-500/40';
    default:
      return 'bg-ink-800/40 text-ink-500 border-ink-700/50';
  }
}

function AiBadge({
  status,
  label,
  icon,
  onClick,
}: {
  status?: string;
  label: string;
  icon: React.ReactNode;
  onClick?: () => void;
}) {
  const cls = `inline-flex items-center justify-center w-6 h-6 rounded-full border ${aiIntentPillClass(status)}`;
  const title = `${label} — ${status || 'not requested'}`;
  if (onClick) {
    return (
      <button type="button" onClick={onClick} title={title} className={`${cls} hover:brightness-125 transition cursor-pointer`}>
        {icon}
      </button>
    );
  }
  return (
    <span title={title} className={cls}>
      {icon}
    </span>
  );
}

/**
 * Row of three AI status chips (Transcript / Summary / Analyze). Status-only by
 * default; pass onClick handlers to make a chip a shortcut (e.g. jump to a tab).
 */
export function AiIntentBadges({
  transcriptStatus,
  summaryStatus,
  analyzeStatus,
  onTranscript,
  onSummary,
  onAnalyze,
  className = '',
}: {
  transcriptStatus?: string;
  summaryStatus?: string;
  analyzeStatus?: string;
  onTranscript?: () => void;
  onSummary?: () => void;
  onAnalyze?: () => void;
  className?: string;
}) {
  return (
    <div className={`flex items-center gap-1.5 justify-end ${className}`}>
      <AiBadge status={transcriptStatus} label="Transcript" icon={<FileText size={11} />} onClick={onTranscript} />
      <AiBadge status={summaryStatus} label="Summary" icon={<Sparkles size={11} />} onClick={onSummary} />
      <AiBadge status={analyzeStatus} label="Analyze" icon={<Eye size={11} />} onClick={onAnalyze} />
    </div>
  );
}

// ── Rating stars ─────────────────────────────────────────────────────────────
export function RatingStars({
  value,
  onChange,
  size = 16,
}: {
  value: number;
  onChange: (v: number) => void;
  size?: number;
}) {
  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((star) => (
        <button key={star} className="p-0 transition-colors" onClick={() => onChange(star === value ? 0 : star)}>
          <Star size={size} className={(value || 0) >= star ? 'text-amber-400 fill-amber-400' : 'text-ink-600'} />
        </button>
      ))}
    </div>
  );
}
