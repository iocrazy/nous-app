import { useVirtualizer } from '@tanstack/react-virtual'
import { columnsForWidth } from '../utils/gridColumns'
import { useContainerWidth } from './useContainerWidth'

interface UseGridVirtualizerOpts {
  scrollRef: React.RefObject<HTMLElement>
  itemCount: number
  estimateRowHeight?: number
  fixedColumns?: number
}

interface UseGridVirtualizerResult {
  columns: number
  rowVirtualizer: ReturnType<typeof useVirtualizer>
  containerRef: (node: HTMLElement | null) => void
  totalSize: number
}

/**
 * Hook: derives column count from the container's measured width via a
 * ResizeObserver, then creates a row-based virtualizer over scrollRef.
 *
 * The caller should attach `containerRef` to the inner content wrapper div
 * (full-width) so the ResizeObserver can measure available layout width.
 *
 * Width measurement is delegated to `useContainerWidth`, which all three grids
 * now share; the callback-ref rationale (and the cold-load 2-column regression
 * it prevents) lives there.
 */
export function useGridVirtualizer({
  scrollRef,
  itemCount,
  estimateRowHeight,
  fixedColumns,
}: UseGridVirtualizerOpts): UseGridVirtualizerResult {
  const { ref: containerRef, width } = useContainerWidth()

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
