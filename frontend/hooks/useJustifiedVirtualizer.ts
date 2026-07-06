import { useCallback, useEffect, useMemo, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { computeJustifiedRows, type JustifiedRow } from '../utils/justifiedLayout'

interface UseJustifiedVirtualizerOpts {
  scrollRef: React.RefObject<HTMLElement>
  /** Display aspect ratios (w/h), one per item, in render order. */
  aspectRatios: number[]
  targetRowHeight?: number
  gap?: number
}

interface UseJustifiedVirtualizerResult {
  /** Pre-computed row partition; item i of row r renders at ar*row.height. */
  rows: JustifiedRow[]
  rowVirtualizer: ReturnType<typeof useVirtualizer>
  containerRef: (node: HTMLDivElement | null) => void
  gap: number
}

/**
 * Virtualizer for the justified (Eagle-style) view. Unlike the uniform grid
 * (useGridVirtualizer), justified rows have VARIABLE heights — so the layout
 * runs as a pure O(n) pre-pass (computeJustifiedRows) and the virtualizer is
 * fed exact per-row sizes, no DOM measurement needed.
 *
 * Width measurement mirrors useGridVirtualizer's callback-ref pattern: the
 * container is conditionally rendered, so a plain RefObject + mount effect
 * would read null and never observe (see that hook's comment).
 */
export function useJustifiedVirtualizer({
  scrollRef,
  aspectRatios,
  targetRowHeight = 170,
  gap = 8,
}: UseJustifiedVirtualizerOpts): UseJustifiedVirtualizerResult {
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(0)

  const containerRef = useCallback((node: HTMLDivElement | null) => {
    setContainerEl(node)
  }, [])

  useEffect(() => {
    if (!containerEl) return
    const initial = containerEl.getBoundingClientRect().width
    if (initial > 0) setWidth(initial)
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setWidth(entry.contentRect.width)
    })
    observer.observe(containerEl)
    return () => observer.disconnect()
  }, [containerEl])

  const rows = useMemo(
    () => computeJustifiedRows(aspectRatios, width, { targetRowHeight, gap }),
    [aspectRatios, width, targetRowHeight, gap],
  )

  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    // Exact size per row (+ the inter-row gap) — no measureElement needed.
    estimateSize: (index) => (rows[index]?.height ?? targetRowHeight) + gap,
    overscan: 4,
  })

  // estimateSize is captured per-measurement: when the row partition changes
  // (resize / new items) the cached sizes go stale — drop them explicitly.
  useEffect(() => {
    rowVirtualizer.measure()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows])

  return { rows, rowVirtualizer, containerRef, gap }
}
