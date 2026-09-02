import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Collects aspect ratios measured from thumbnails as they load, BATCHED to one
 * state update per animation frame.
 *
 * Why batching is not optional: a screenful of the adaptive view is ~30 cards,
 * and their images resolve in a burst. Reporting each one straight into state
 * would run the justified layout pre-pass and re-virtualize 30 times in a few
 * milliseconds — visible as rows twitching while a folder opens. Coalescing
 * per frame means the rows settle in one re-layout.
 *
 * GROWTH — the map is NOT pruned, and nothing bounds it below the size of the
 * list. It gains one entry per resource id whose thumbnail has been measured,
 * and those entries live for the lifetime of the provider, so scrolling a large
 * folder end to end leaves an entry per item seen (plus the same again in the
 * `knownRef` mirror). Two numbers keyed by id is a few dozen bytes each, so a
 * 100k-item folder scrolled in full costs single-digit MB — acceptable, but it
 * is a ceiling worth knowing rather than a claim that it stays small.
 *
 * Pruning is deliberately not attempted: entries are only re-derivable by
 * re-decoding the image, so evicting one trades memory for a re-measure and a
 * re-layout ripple (see utils/justifiedLayout.ts) the moment the user scrolls
 * back. Dropping the whole map on a folder change would be the cheap win if
 * this ever matters.
 */
export interface MeasuredAspectRatios {
  /** id → measured ratio (w/h). Identity changes only on a real flush. */
  measured: Record<string, number>
  /** Report a thumbnail's natural ratio. Cheap and idempotent. */
  report: (id: string, aspect: number) => void
}

export function useMeasuredAspectRatios(): MeasuredAspectRatios {
  const [measured, setMeasured] = useState<Record<string, number>>({})

  // Staging area for the current frame's reports.
  const pendingRef = useRef<Record<string, number>>({})
  const frameRef = useRef<number | null>(null)
  // Mirror of `measured` readable synchronously, so a duplicate report inside
  // the same frame can be dropped without waiting for the state to land.
  const knownRef = useRef<Record<string, number>>({})
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current)
        frameRef.current = null
      }
    }
  }, [])

  const flush = useCallback(() => {
    frameRef.current = null
    const batch = pendingRef.current
    pendingRef.current = {}
    if (!mountedRef.current) return
    const keys = Object.keys(batch)
    if (keys.length === 0) return
    setMeasured((prev) => {
      // Re-check against the committed map: entries can be superseded between
      // the report and the flush.
      const changed = keys.filter((k) => prev[k] !== batch[k])
      if (changed.length === 0) return prev
      return { ...prev, ...batch }
    })
  }, [])

  const report = useCallback(
    (id: string, aspect: number) => {
      if (!id) return
      if (!Number.isFinite(aspect) || aspect <= 0) return
      // Already recorded (or already staged) with the same value — no-op.
      if (knownRef.current[id] === aspect) return
      knownRef.current[id] = aspect
      pendingRef.current[id] = aspect
      if (frameRef.current === null) {
        frameRef.current = requestAnimationFrame(flush)
      }
    },
    [flush],
  )

  return { measured, report }
}
