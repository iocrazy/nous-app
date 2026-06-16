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
import { X, type LucideIcon } from 'lucide-react';
import { islandUI } from '../../../utils/featureFlags';

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
  /** Optional lucide icon rendered inside the chip ahead of the label. */
  icon?: LucideIcon;
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
  icon: Icon,
  children,
}) => {
  const rootRef = useRef<HTMLDivElement>(null);
  const island = islandUI();

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
            : island
              ? 'border-line bg-island-2 text-content-2 hover:border-line-strong hover:text-content'
              : 'border-ink-700/80 bg-ink-900/40 text-ink-300 hover:border-ink-600 hover:text-ink-100'
        }`}
      >
        <button
          type="button"
          onClick={onToggle}
          className="px-2.5 py-1 text-xs font-medium whitespace-nowrap focus:outline-none inline-flex items-center gap-1.5"
          aria-haspopup="menu"
          aria-expanded={isOpen}
        >
          {Icon && <Icon size={12} aria-hidden="true" />}
          <span>{displayLabel}</span>
        </button>
        {isActive && onClear && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onClear();
            }}
            className={`pr-1.5 pl-0.5 py-1 ${island ? 'text-content-2 hover:text-content' : 'text-ink-400 hover:text-ink-100'}`}
            aria-label={`Clear ${label}`}
          >
            <X size={12} />
          </button>
        )}
      </div>
      {isOpen && (
        <div className={`absolute left-0 top-full mt-1.5 z-30 min-w-[14rem] ${island ? 'bg-card' : 'bg-ink-900/95'} backdrop-blur-sm border ${island ? 'border-line' : 'border-ink-700/80'} rounded-xl shadow-2xl animate-dropdown`}>
          {children}
        </div>
      )}
    </div>
  );
};
