import { useRef, useState, useEffect } from 'react'
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
  containerRef: React.RefObject<HTMLDivElement>
  totalSize: number
}

/**
 * Hook: derives column count from the container's measured width via a
 * ResizeObserver, then creates a row-based virtualizer over scrollRef.
 *
 * The caller should attach `containerRef` to the inner content wrapper div
 * (full-width) so the ResizeObserver can measure available layout width.
 */
export function useGridVirtualizer({
  scrollRef,
  itemCount,
  estimateRowHeight,
  fixedColumns,
}: UseGridVirtualizerOpts): UseGridVirtualizerResult {
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  // Observe the container div width; clean up on unmount.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) {
        setWidth(entry.contentRect.width)
      }
    })
    observer.observe(el)
    return () => {
      observer.disconnect()
    }
  }, [])

  // isMobile: treat container widths below 768px as mobile.
  const isMobile = width > 0 && width < 768
  const columns = (fixedColumns && fixedColumns > 0) ? fixedColumns : columnsForWidth(width, isMobile)

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
