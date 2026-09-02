import { useCallback, useState, useEffect } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { columnsForWidth } from '../utils/gridColumns'

interface UseGridVirtualizerOpts {
  scrollRef: React.RefObject<HTMLElement>
  itemCount: number
  estimateRowHeight?: number
  fixedColumns?: number
}

interface UseGridVirtualizerResult {
  columns: number
  rowVirtualizer: ReturnType<typeof useVirtualizer>
  containerRef: (node: HTMLDivElement | null) => void
  totalSize: number
}

/**
 * Hook: derives column count from the container's measured width via a
 * ResizeObserver, then creates a row-based virtualizer over scrollRef.
 *
 * The caller should attach `containerRef` to the inner content wrapper div
 * (full-width) so the ResizeObserver can measure available layout width.
 *
 * `containerRef` is a CALLBACK ref (not a RefObject) on purpose: the grid
 * container is conditionally rendered (only when there are items), so it
 * mounts AFTER the first render on a cold load. A `useEffect(..., [])` reading
 * a RefObject would run once at mount, find the ref null, and never attach the
 * observer — leaving width stuck at 0 → columnsForWidth(0) → a permanent
 * 2-column layout. The callback-ref state node re-runs the observer effect the
 * moment the div attaches, and measures immediately so we don't render one
 * frame at the fallback width.
 */
export function useGridVirtualizer({
  scrollRef,
  itemCount,
  estimateRowHeight,
  fixedColumns,
}: UseGridVirtualizerOpts): UseGridVirtualizerResult {
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(0)

  // Callback ref: fires with the node when the (conditionally-rendered)
  // container attaches and with null when it detaches, driving the effect below.
  const containerRef = useCallback((node: HTMLDivElement | null) => {
    setContainerEl(node)
  }, [])

  // Observe the container div width; re-runs whenever the node (re)attaches.
  useEffect(() => {
    if (!containerEl) return

    // Measure synchronously on attach so the first painted frame already has
    // the real width (the ResizeObserver callback lands a frame later).
    const initial = containerEl.getBoundingClientRect().width
    if (initial > 0) setWidth(initial)

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) {
        setWidth(entry.contentRect.width)
      }
    })
    observer.observe(containerEl)
    return () => {
      observer.disconnect()
    }
  }, [containerEl])

  // Column count is derived from the container width alone, via a card-width
  // band. There is deliberately no "mobile" branch here: this hook only ever
  // saw the CONTAINER width, so a `width < 768` test fired whenever the info
  // panel opened on a normal desktop and collapsed the grid into two giant
  // columns. See utils/gridColumns.ts for the full history.
  const columns = (fixedColumns && fixedColumns > 0) ? fixedColumns : columnsForWidth(width)

  // Guard: columns must be >= 1 to avoid division-by-zero in Math.ceil.
  const safeColumns = Math.max(1, columns)
  const rowCount = Math.ceil(itemCount / safeColumns)

  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateRowHeight ?? 280,
    overscan: 3,
  })

  return {
    columns,
    rowVirtualizer,
    containerRef,
    totalSize: rowVirtualizer.getTotalSize(),
  }
}
