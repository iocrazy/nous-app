// frontend/components/resources/filter/FilterChip.tsx
//
// Generic chip used by the Resources filter bar. The chip is a labelled
// button with an optional summary (e.g. "Tags · 3") and an optional
// "clear" affordance. Clicking the chip body toggles a dropdown whose
// contents are supplied by the parent as `children`.
//
// Positioning: the dropdown floats under the chip using plain absolute
// positioning — the bar itself should provide `relative` ancestry so
// dropdowns never cover sibling chips.

import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

export interface FilterChipProps {
  /** Machine id, surfaced as data attribute for tests. */
  chipId: string;
  /** Translated label always shown in the chip body. */
  label: string;
  /** When the chip is active, shown after the label as " · {summary}". */
  activeSummary?: string | null;
  /** Whether the chip currently has a non-default value. */
  isActive: boolean;
  /** Whether this chip's dropdown is currently open. */
  isOpen: boolean;
  /** Toggle dropdown open/closed. */
  onToggle: () => void;
  /** Called when dropdown should close (outside click, Esc). */
  onClose: () => void;
  /** Clear this chip's value. Omit to hide the clear X. */
  onClear?: () => void;
  /** Dropdown content rendered when open. */
  children: React.ReactNode;
}

export const FilterChip: React.FC<FilterChipProps> = ({
  chipId,
  label,
  activeSummary,
  isActive,
  isOpen,
  onToggle,
  onClose,
  onClear,
  children,
}) => {
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click / Esc.
  useEffect(() => {
    if (!isOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
    };
  }, [isOpen, onClose]);

  const displayLabel = isActive && activeSummary ? `${label} · ${activeSummary}` : label;

  return (
    <div ref={rootRef} className="relative" data-chip-id={chipId}>
      <div
        className={`inline-flex items-center rounded-lg border transition-colors ${
          isActive
            ? 'border-indigo-500/60 bg-indigo-500/10 text-indigo-300'
            : 'border-zinc-700/80 bg-zinc-900/40 text-zinc-300 hover:border-zinc-600 hover:text-zinc-100'
        }`}
      >
        <button
          type="button"
          onClick={onToggle}
          className="px-2.5 py-1 text-xs font-medium whitespace-nowrap focus:outline-none"
          aria-haspopup="menu"
          aria-expanded={isOpen}
        >
          {displayLabel}
        </button>
        {isActive && onClear && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onClear();
            }}
            className="pr-1.5 pl-0.5 py-1 text-zinc-400 hover:text-zinc-100"
            aria-label={`Clear ${label}`}
          >
            <X size={12} />
          </button>
        )}
      </div>
      {isOpen && (
        <div className="absolute left-0 top-full mt-1.5 z-30 min-w-[14rem] bg-zinc-900/95 backdrop-blur-sm border border-zinc-700/80 rounded-xl shadow-2xl animate-dropdown">
          {children}
        </div>
      )}
    </div>
  );
};
