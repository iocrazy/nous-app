import { useEffect, useMemo, useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { computeJustifiedRows, type JustifiedRow } from '../utils/justifiedLayout'
import { useContainerWidth } from './useContainerWidth'

/** Height of a grid card's non-thumbnail chrome (filename bar): px-3/py-2.5
 *  padding + one 13px text line + border. Estimate only — rows re-measure. */
const CARD_CHROME_ESTIMATE = 44

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
  containerRef: (node: HTMLElement | null) => void
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
  const { ref: containerRef, width } = useContainerWidth()

  // Previous inputs/outputs, so a repeat layout of the same list can keep the
  // untouched prefix instead of repacking from item 0. See the ripple note at
  // the top of utils/justifiedLayout.ts for what this does and does NOT fix.
  const prevRatiosRef = useRef<number[]>([])
  const prevRowsRef = useRef<JustifiedRow[]>([])
  const prevShapeRef = useRef('')

  const rows = useMemo(() => {
    const shape = `${width}|${targetRowHeight}|${gap}|${aspectRatios.length}`
    const sameShape = shape === prevShapeRef.current
    const prevRatios = prevRatiosRef.current
    const prevRows = prevRowsRef.current

    let firstChangedIndex = 0
    if (sameShape && prevRows.length > 0) {
      firstChangedIndex = aspectRatios.length
      for (let i = 0; i < aspectRatios.length; i += 1) {
        if (aspectRatios[i] !== prevRatios[i]) {
          firstChangedIndex = i
          break
        }
      }
      // Nothing moved at all — hand back the SAME array so the virtualizer's
      // measure effect (keyed on `rows` identity) does not fire needlessly.
      if (firstChangedIndex === aspectRatios.length) return prevRows
    }

    const next = computeJustifiedRows(
      aspectRatios,
      width,
      { targetRowHeight, gap },
      sameShape && prevRows.length > 0
        ? { prevRows, firstChangedIndex }
        : undefined,
    )

    prevRatiosRef.current = aspectRatios
    prevRowsRef.current = next
    prevShapeRef.current = shape
    return next
  }, [aspectRatios, width, targetRowHeight, gap])

  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    // The layout pass gives the exact IMAGE height per row, but each card
    // renders extra chrome below the thumbnail (the filename info bar,
    // ~40px; more with the temp-TTL strip or a transcoding badge). The
    // estimate covers the common case and rows self-correct via
    // measureElement, so stacked chrome never overlaps the next row.
    estimateSize: (index) =>
      (rows[index]?.height ?? targetRowHeight) + CARD_CHROME_ESTIMATE + gap,
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
