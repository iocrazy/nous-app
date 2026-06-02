import React, { useState, useRef, useCallback } from 'react';
import { Star } from 'lucide-react';

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

/** Drag-to-resize state for a right-hand detail panel. */
export function useResizablePanel(initial: number = DEFAULT_PANEL_WIDTH) {
  const [panelWidth, setPanelWidth] = useState(initial);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(initial);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragStartX.current = e.clientX;
    dragStartWidth.current = panelWidth;
    const onMove = (ev: MouseEvent) => {
      // Panel sits on the RIGHT, so dragging left (smaller clientX) widens it.
      const delta = dragStartX.current - ev.clientX;
      const newWidth = Math.min(MAX_PANEL_WIDTH, Math.max(MIN_PANEL_WIDTH, dragStartWidth.current + delta));
      setPanelWidth(newWidth);
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [panelWidth]);

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
  'bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden hover:border-zinc-700 transition-all duration-300 shadow-lg flex flex-col max-w-full';

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
      <span className="px-2 py-1 text-xs font-semibold bg-indigo-900/30 text-indigo-400 rounded-md border border-indigo-900/50">
        {children}
      </span>
    );
  }
  return (
    <span className="px-2 py-1 text-xs font-semibold bg-zinc-800 text-zinc-300 rounded-md border border-zinc-700 uppercase tracking-wider">
      {children}
    </span>
  );
}

// ── Stat grid (the signature mini-card row) ──────────────────────────────────
export function StatGrid({ cols, className = '', children }: { cols: 3 | 4; className?: string; children: React.ReactNode }) {
  return (
    <div className={`grid ${cols === 3 ? 'grid-cols-3' : 'grid-cols-4'} gap-2 sm:gap-4 ${className}`}>
      {children}
    </div>
  );
}

const STAT_CARD_BASE =
  'flex flex-col items-center justify-center p-2 sm:p-3 bg-zinc-950 rounded-xl border border-zinc-800';

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
      <span className="text-xs sm:text-sm font-bold text-white">{value}</span>
      <span className="text-[9px] sm:text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">{label}</span>
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
    <h4 className={`text-[11px] font-semibold text-zinc-500 uppercase tracking-widest ${className}`}>
      {children}
    </h4>
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
          <Star size={size} className={(value || 0) >= star ? 'text-amber-400 fill-amber-400' : 'text-zinc-600'} />
        </button>
      ))}
    </div>
  );
}
