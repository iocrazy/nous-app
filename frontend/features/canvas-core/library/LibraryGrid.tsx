// features/canvas-core/library/LibraryGrid.tsx
//
// THE canvas library grid — one implementation behind every picker.
//
// Search box, kind chips, justified multi-select grid, a consequence line and
// an action footer. It is deliberately fed ONE store's rows at a time: the `@`
// palette renders four groups by mounting it per group, the panel renders one
// segment at a time, and neither needs the component to know which store it is
// looking at.
//
// The consequence line is a REQUIRED prop, not an optional one. Spec §2 goal 6
// is that every pick says what it will do — "adds a reference" and "inserts a
// mention" and "replaces the body" are three different outcomes behind
// identically-shaped grids, and an optional prop is one a caller forgets.
//
// Layout reuses PR #2092's two item-agnostic pieces (`computeJustifiedRows`
// via `useJustifiedVirtualizer`). WARNING: When the scroll container has no
// measured height — jsdom, and the first frame of a panel that has not laid
// out yet — the virtualizer reports ZERO virtual rows. Rendering the full row
// list in that case is what keeps the grid from painting blank; the lists here
// are capped at 60 rows by `useLibrarySearch`, so the fallback is cheap.

import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RotateCw, Search } from 'lucide-react';

import { useJustifiedVirtualizer } from '../../../hooks/useJustifiedVirtualizer';
import { useMeasuredAspectRatios } from '../../../hooks/useMeasuredAspectRatios';
import { LibraryCell } from './LibraryCell';
import type { LibraryItem } from './librarySearch';
import {
  applyPick,
  libraryKey,
  selectedItems,
  type LibraryItemKey,
  type SelectionState,
} from './librarySelection';

const GAP = 6;

export interface LibraryKindChip {
  value: string | null;
  label: string;
}

export interface LibraryGridAction {
  label: string;
  disabled?: boolean;
  onClick: (items: LibraryItem[]) => void;
}

export interface LibraryGridProps {
  testId?: string;
  items: LibraryItem[];
  loading?: boolean;
  error?: Error | null;
  onRetry?: () => void;
  query: string;
  onQueryChange: (q: string) => void;
  searchPlaceholder: string;
  kinds?: readonly LibraryKindChip[];
  activeKind?: string | null;
  onKindChange?: (kind: string | null) => void;
  selection: readonly LibraryItemKey[];
  onSelectionChange: (keys: LibraryItemKey[]) => void;
  /** First line: what picking these will DO. NOT optional — spec §2 goal 6. */
  consequence: string;
  /** Files that will really be sent after the model's ceiling, or null when
   *  there is no target and the question has no answer. */
  fileCount?: number | null;
  /** A refusal in words, e.g. "3 / 3 references used · remove one on the node". */
  note?: string | null;
  primaryAction?: LibraryGridAction;
  secondaryAction?: LibraryGridAction;
  onItemActivate?: (item: LibraryItem) => void;
  onItemHover?: (item: LibraryItem | null, rect?: DOMRect) => void;
  onItemDragStart?: (item: LibraryItem, e: React.DragEvent) => void;
  emptyLabel: string;
  targetRowHeight?: number;
  className?: string;
}

export function LibraryGrid({
  testId = 'library-grid',
  items,
  loading = false,
  error = null,
  onRetry,
  query,
  onQueryChange,
  searchPlaceholder,
  kinds,
  activeKind = null,
  onKindChange,
  selection,
  onSelectionChange,
  consequence,
  fileCount = null,
  note = null,
  primaryAction,
  secondaryAction,
  onItemActivate,
  onItemHover,
  onItemDragStart,
  emptyLabel,
  targetRowHeight = 120,
  className = '',
}: LibraryGridProps): React.ReactElement {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<number | null>(null);
  const [active, setActive] = useState(0);

  const { measured, report } = useMeasuredAspectRatios();
  const aspectRatios = useMemo(
    () => items.map((i) => measured[libraryKey(i)] ?? i.aspect ?? 1),
    [items, measured],
  );
  const { rows, rowVirtualizer, containerRef } = useJustifiedVirtualizer({
    scrollRef: scrollRef as React.RefObject<HTMLElement>,
    aspectRatios,
    targetRowHeight,
    gap: GAP,
  });
  const virtual = rowVirtualizer.getVirtualItems();
  const visibleRows = virtual.length > 0 ? virtual.map((v) => v.index) : rows.map((_, i) => i);

  const chosen = useMemo(() => selectedItems(selection, items), [selection, items]);
  const selectedSet = useMemo(() => new Set(selection), [selection]);

  const pick = useCallback(
    (index: number, e: React.MouseEvent) => {
      const state: SelectionState = { keys: [...selection], anchor: anchorRef.current };
      const next = applyPick(state, items, index, {
        meta: e.metaKey || e.ctrlKey,
        shift: e.shiftKey,
      });
      anchorRef.current = next.anchor;
      setActive(index);
      onSelectionChange(next.keys);
    },
    [selection, items, onSelectionChange],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        setActive((i) =>
          Math.max(0, Math.min(items.length - 1, i + (e.key === 'ArrowRight' ? 1 : -1))),
        );
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        if (chosen.length > 0 && primaryAction && !primaryAction.disabled) {
          primaryAction.onClick(chosen);
          return;
        }
        const item = items[active];
        if (item) onItemActivate?.(item);
      }
    },
    [items, active, chosen, primaryAction, onItemActivate],
  );

  return (
    <div
      data-testid={testId}
      onKeyDown={onKeyDown}
      className={`nodrag nowheel nopan flex min-h-0 flex-col ${className}`}
    >
      <div className="flex items-center gap-1.5 border-b border-canvas-line px-2 py-1.5">
        <Search size={13} className="shrink-0 text-canvas-muted" />
        <input
          data-testid="library-search"
          aria-label={t('canvas.library.searchLabel', 'Search Library')}
          placeholder={searchPlaceholder}
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          className="min-w-0 flex-1 bg-transparent text-xs text-canvas-text outline-none placeholder:text-canvas-muted"
        />
      </div>

      <p
        data-testid="library-consequence"
        className="border-b border-canvas-line px-2 py-1 text-[10px] leading-snug text-canvas-muted"
      >
        {consequence}
      </p>

      {kinds && kinds.length > 0 && (
        <div className="flex flex-wrap gap-1 border-b border-canvas-line px-2 py-1.5">
          {kinds.map((chip) => (
            <button
              key={chip.value ?? '__all'}
              type="button"
              data-testid={`library-kind-${chip.value ?? 'all'}`}
              aria-pressed={activeKind === chip.value}
              onClick={() => onKindChange?.(chip.value)}
              className={`nodrag rounded-full border px-2 py-0.5 text-[10px] ${
                activeKind === chip.value
                  ? 'border-[var(--accent-border)] text-[var(--accent-text)]'
                  : 'border-canvas-line text-canvas-muted hover:text-canvas-text'
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>
      )}

      <div ref={scrollRef} className="nowheel min-h-0 flex-1 overflow-y-auto p-1.5">
        {error ? (
          <div data-testid="library-error" className="p-3 text-[11px] text-warn">
            <p>{t('canvas.library.loadFailed', 'Could not load this library')}</p>
            {onRetry && (
              <button
                type="button"
                data-testid="library-retry"
                onClick={onRetry}
                className="nodrag mt-1 inline-flex items-center gap-1 rounded border border-canvas-line px-2 py-0.5 text-canvas-text"
              >
                <RotateCw size={11} />
                {t('canvas.library.retry', 'Retry')}
              </button>
            )}
          </div>
        ) : loading && items.length === 0 ? (
          <div className="flex items-center gap-2 p-3 text-[11px] text-canvas-muted">
            <Loader2 size={12} className="animate-spin" />
            {t('canvas.library.loading', 'Loading…')}
          </div>
        ) : items.length === 0 ? (
          <div data-testid="library-empty" className="p-3 text-[11px] text-canvas-muted">
            {emptyLabel}
          </div>
        ) : (
          <div ref={containerRef} className="flex flex-col" style={{ gap: GAP }}>
            {visibleRows.map((rowIndex) => {
              const row = rows[rowIndex];
              if (!row) return null;
              return (
                <div key={rowIndex} className="flex" style={{ gap: GAP, height: row.height }}>
                  {items.slice(row.start, row.end).map((item, offset) => {
                    const index = row.start + offset;
                    return (
                      <LibraryCell
                        key={libraryKey(item)}
                        item={item}
                        width={aspectRatios[index] * row.height}
                        height={row.height}
                        selected={selectedSet.has(libraryKey(item))}
                        active={index === active}
                        onPick={(e) => pick(index, e)}
                        onActivate={() => onItemActivate?.(item)}
                        onMeasure={(a) => report(libraryKey(item), a)}
                        onHoverStart={(rect) => onItemHover?.(item, rect)}
                        onHoverEnd={() => onItemHover?.(null)}
                        onDragStart={
                          onItemDragStart ? (e) => onItemDragStart(item, e) : undefined
                        }
                      />
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-canvas-line px-2 py-1.5">
        <span data-testid="library-footer-count" className="text-[10px] text-canvas-muted">
          {fileCount === null || fileCount === undefined
            ? t('canvas.library.selected', {
                count: chosen.length,
                defaultValue: '{{count}} selected',
              })
            : `${t('canvas.library.selected', {
                count: chosen.length,
                defaultValue: '{{count}} selected',
              })} · ${t('canvas.library.files', {
                count: fileCount,
                defaultValue: '{{count}} files',
              })}`}
        </span>
        <span className="flex-1" />
        {secondaryAction && (
          <button
            type="button"
            data-testid="library-secondary"
            disabled={secondaryAction.disabled || chosen.length === 0}
            onClick={() => secondaryAction.onClick(chosen)}
            className="nodrag rounded border border-canvas-line px-2 py-0.5 text-[11px] text-canvas-text disabled:cursor-not-allowed disabled:opacity-50"
          >
            {secondaryAction.label}
          </button>
        )}
        {primaryAction && (
          <button
            type="button"
            data-testid="library-primary"
            disabled={primaryAction.disabled || chosen.length === 0}
            onClick={() => primaryAction.onClick(chosen)}
            className="nodrag rounded border border-[var(--accent-border)] px-2 py-0.5 text-[11px] text-[var(--accent-text)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {primaryAction.label}
          </button>
        )}
      </div>
      {note && (
        <p data-testid="library-note" className="px-2 pb-1.5 text-[10px] text-warn">
          {note}
        </p>
      )}
    </div>
  );
}

export default LibraryGrid;
