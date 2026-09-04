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

  // Close on outside click / Esc.
  useEffect(() => {
    if (!isOpen) return;
    /**
     * A floating layer that a dropdown opened ON TOP of itself — the date
     * chip's DateTimePopover renders through `createPortal(document.body)`,
     * so it is visually inside this dropdown but DOM-wise outside `rootRef`.
     * Without this, clicking a calendar day reads as an outside click and
     * closes the chip out from under the picker.
     *
     * The `!contains(rootRef)` clause keeps the ordinary case intact: a modal
     * dialog that CONTAINS this chip is not a layer above it, so clicking the
     * modal's own body still closes the dropdown exactly as before.
     */
    const inFloatingLayer = (target: EventTarget | null): boolean => {
      if (!(target instanceof Element)) return false;
      const layer = target.closest('[role="dialog"]');
      return !!layer && !!rootRef.current && !layer.contains(rootRef.current);
    };
    const handleClick = (e: MouseEvent) => {
      if (inFloatingLayer(e.target)) return;
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      // Escape belongs to the topmost layer: while a portalled popover is
      // open it dismisses that, not the dropdown underneath.
      const openLayer = document.querySelector('[role="dialog"]');
      if (openLayer && rootRef.current && !openLayer.contains(rootRef.current)) return;
      onClose();
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
    // `w-fit` is load-bearing, not cosmetic: this root is the containing block
    // the panel's `min-w-full` resolves against. A bare block-level root
    // stretches to its parent, so a chip mounted in a plain block container
    // would get a panel as wide as the page. Every bar today mounts chips as
    // flex items (content-sized already), which is exactly why that failure
    // mode would ship silently — no test and no existing screen would show it.
    // fit-content is what a flex item already computes, so this changes
    // nothing for the current callers and makes the contract independent of
    // how the caller lays out.
    <div ref={rootRef} className="relative w-fit" data-chip-id={chipId}>
      <div
        className={`inline-flex items-center rounded-lg border transition-colors ${
          isActive
            ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
            : 'border-line bg-island-2 text-content-2 hover:border-line-strong hover:text-content'
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
            className="pr-1.5 pl-0.5 py-1 text-content-2 hover:text-content"
            aria-label={`Clear ${label}`}
          >
            <X size={12} />
          </button>
        )}
      </div>
      {isOpen && (
        // Sizing follows the app-wide UiSelect contract (see
        // `ui/UiSelect.menuWidth.test.tsx`): no width, just a floor and a
        // ceiling, so the panel is whatever its content needs. This used to be
        // `min-w-[14rem]`, which stacked with each body's own fixed `w-44` /
        // `w-48` / `w-56` — the 224px floor won on every body narrower than
        // that and left dead space down the panel's right edge.
        //   w-max        shrink-to-fit
        //   min-w-full   100% of this `relative` root, i.e. the chip itself,
        //                so a wide chip never sits above a narrower panel
        //   max-w-[90vw] never runs off the viewport
        //   overflow-hidden  `w-max` gives up the shrink-to-fit clamp a plain
        //                `width:auto` box would have had, so a body wide
        //                enough to beat the ceiling would otherwise paint
        //                OUTSIDE these rounded borders. Bodies whose rows can
        //                hold user-authored text carry their own max-w too —
        //                a `truncate` row never truncates inside a `w-max`
        //                parent, it just makes the box wider.
        <div className="absolute left-0 top-full mt-1.5 z-30 w-max min-w-full max-w-[90vw] overflow-hidden bg-card backdrop-blur-sm border border-line rounded-xl shadow-2xl animate-dropdown">
          {children}
        </div>
      )}
    </div>
  );
};
