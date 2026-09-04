// features/canvas-core/library/LibraryCell.tsx
//
// One justified cell. Presentational: it owns no selection state and no
// fetching, so the same cell serves the compact reference popover and the
// full panel.
//
// `nodrag nowheel nopan` are load-bearing, not decoration — this grid renders
// inside a React Flow node in its popover form, and React Flow stops mousedown
// propagation on the node, so without them every cell silently does nothing
// for a real user while a synthetic dispatch in a test still "works".

import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ImageOff } from 'lucide-react';

import type { LibraryItem } from './librarySearch';

export interface LibraryCellProps {
  item: LibraryItem;
  width: number;
  height: number;
  selected: boolean;
  active: boolean;
  onPick: (e: React.MouseEvent) => void;
  onActivate: () => void;
  /** The thumbnail's natural w/h, once the browser knows it. */
  onMeasure: (aspect: number) => void;
  onHoverStart?: (rect: DOMRect) => void;
  onHoverEnd?: () => void;
  onDragStart?: (e: React.DragEvent) => void;
}

export function LibraryCell({
  item,
  width,
  height,
  selected,
  active,
  onPick,
  onActivate,
  onMeasure,
  onHoverStart,
  onHoverEnd,
  onDragStart,
}: LibraryCellProps): React.ReactElement {
  const { t } = useTranslation();

  const handleLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      if (img.naturalWidth > 0 && img.naturalHeight > 0) {
        onMeasure(img.naturalWidth / img.naturalHeight);
      }
    },
    [onMeasure],
  );

  return (
    <button
      type="button"
      data-testid="library-cell"
      data-library-store={item.store}
      data-library-id={item.id}
      data-active={active ? 'true' : undefined}
      aria-pressed={selected}
      draggable={onDragStart !== undefined}
      onDragStart={onDragStart}
      onClick={onPick}
      onDoubleClick={onActivate}
      onMouseEnter={(e) => onHoverStart?.(e.currentTarget.getBoundingClientRect())}
      onMouseLeave={() => onHoverEnd?.()}
      style={{ width, height }}
      className={`nodrag nowheel nopan group relative shrink-0 overflow-hidden rounded-md border text-left transition-colors ${
        selected
          ? 'border-[var(--accent-border)]'
          : 'border-canvas-line hover:border-[var(--accent-border)]'
      } ${
        // Two states, two channels: the BORDER says "picked", the RING says
        // "the keyboard cursor is here". They are mutually exclusive as rings
        // because two ring utilities on one element would race on stylesheet
        // order rather than on class order.
        //
        // The offset needs its own COLOUR. Tailwind's default offset colour is
        // white, so `ring-offset-1` alone drew a white halo around the cursor
        // cell on the dark canvas. `--canvas-card` is the theme-aware surface
        // this cell sits on (#171d29 dark / #ffffff light), so the offset reads
        // as a gap in both themes rather than as a second ring.
        active
          ? 'ring-2 ring-[var(--accent-text)] ring-offset-1 ring-offset-[var(--canvas-card)]'
          : selected
            ? 'ring-1 ring-[var(--accent-border)]'
            : ''
      }`}
    >
      {item.thumbUrl ? (
        <img
          src={item.thumbUrl}
          alt=""
          loading="lazy"
          onLoad={handleLoad}
          className="h-full w-full object-cover"
        />
      ) : (
        <span className="flex h-full w-full items-center justify-center bg-canvas-card text-canvas-muted">
          <ImageOff size={14} />
        </span>
      )}
      <span className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-black/55 px-1 py-0.5 text-[10px] text-white">
        {item.title}
      </span>
      {item.ready === false && (
        <span
          data-testid="library-cell-not-ready"
          className="pointer-events-none absolute left-1 top-1 rounded bg-warn-soft px-1 text-[9px] text-warn"
        >
          {t('canvas.library.notReady', 'Not Ready')}
        </span>
      )}
    </button>
  );
}

export default LibraryCell;
