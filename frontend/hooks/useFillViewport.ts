import { useEffect, useRef } from 'react'

interface UseFillViewportOpts {
  sentinelRef: React.RefObject<HTMLElement | null>
  scrollRef: React.RefObject<HTMLElement | null>
  /** False while a page is loading, when there is no next page, or in search mode. */
  enabled: boolean
  /** Changes whenever a page lands (the item count). */
  contentKey: number
  loadMore: () => void
}

/**
 * Keeps loading pages until the list overflows the screen.
 *
 * The IntersectionObserver used for infinite scroll only fires on a
 * visibility TRANSITION. When page 1 is too short to fill the screen, the
 * sentinel stays on screen after the page lands — no transition, no scroll
 * event — so the list stopped at one page with blank space and a Load More
 * button below it.
 *
 * This check runs once per content change and stops by construction: as soon
 * as the sentinel sits below the visible bottom of the scroller, nothing
 * happens. Each content length is attempted at most once, so a failed load
 * (length unchanged) does not turn into a retry loop.
 */
export function useFillViewport({
  sentinelRef,
  scrollRef,
  enabled,
  contentKey,
  loadMore,
}: UseFillViewportOpts): void {
  const attemptedKeyRef = useRef<number | null>(null)

  useEffect(() => {
    if (!enabled || attemptedKeyRef.current === contentKey) return
    const rafId = requestAnimationFrame(() => {
      const sentinel = sentinelRef.current
      if (!sentinel) return
      // Desktop scrolls inside the content container, mobile scrolls the
      // window; the visible bottom is whichever edge comes first.
      const scrollerBottom = scrollRef.current?.getBoundingClientRect().bottom ?? Infinity
      const visibleBottom = Math.min(window.innerHeight, scrollerBottom)
      if (sentinel.getBoundingClientRect().top <= visibleBottom) {
        attemptedKeyRef.current = contentKey
        loadMore()
      }
    })
    return () => cancelAnimationFrame(rafId)
  }, [enabled, contentKey, sentinelRef, scrollRef, loadMore])
}
