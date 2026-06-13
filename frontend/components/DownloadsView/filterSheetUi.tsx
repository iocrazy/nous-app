// frontend/components/DownloadsView/filterSheetUi.tsx
//
// Shared presentational pieces for the mobile filter sheet: a selectable Pill
// and a CollapsibleSection (accordion row). Kept separate so MobileFilterSheet
// and TagFilterSection share one source of truth.

import { ChevronDown } from 'lucide-react';

export function Pill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
        active
          ? 'bg-indigo-500 border-indigo-400 text-white'
          : 'bg-ink-800 border-ink-700 text-ink-300 active:bg-ink-700'
      }`}
    >
      {children}
    </button>
  );
}

/**
 * One accordion row. Collapsed by default so the sheet stays compact even with
 * 9 sections — the header carries a short `summary` badge when the section has
 * active filters, so users see their active state without expanding.
 */
export function CollapsibleSection({
  title,
  summary,
  open,
  onToggle,
  children,
}: {
  title: string;
  /** Short active-state hint (e.g. "2", "Video", "≥3★"). null/empty = inactive. */
  summary?: string | null;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="border-b border-ink-800/70 last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between py-3 text-left"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium text-ink-50">{title}</span>
          {summary ? (
            <span className="max-w-[140px] truncate px-1.5 h-[18px] rounded-full bg-indigo-500/90 text-white text-[10px] font-semibold inline-flex items-center">
              {summary}
            </span>
          ) : null}
        </div>
        <ChevronDown
          size={16}
          className={`shrink-0 text-ink-500 transition-transform ${
            open ? 'rotate-180' : ''
          }`}
        />
      </button>
      {open && <div className="pb-4">{children}</div>}
    </div>
  );
}
