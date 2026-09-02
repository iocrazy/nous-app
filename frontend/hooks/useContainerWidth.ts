import { useCallback, useEffect, useState } from 'react'

/**
 * Measures a container's layout width via ResizeObserver.
 *
 * `ref` is a CALLBACK ref, not a RefObject, on purpose: grid containers here
 * are conditionally rendered (they mount only once there are items), so on a
 * cold load the node attaches AFTER the first render. A `useEffect(..., [])`
 * reading a RefObject would run once at mount, find null, never attach the
 * observer, and leave the width pinned at 0 — which downstream reads as "not
 * measured yet" forever. Driving the effect off callback-ref state re-runs it
 * the moment the node attaches.
 *
 * The width is also read synchronously on attach so the first painted frame
 * already has the real number; the observer callback lands a frame later.
 *
 * Shared by the resource grid's justified virtualizer and the downloads grid,
 * so the two cannot drift on this detail.
 */
export function useContainerWidth(): {
  ref: (node: HTMLElement | null) => void
  width: number
} {
  const [containerEl, setContainerEl] = useState<HTMLElement | null>(null)
  const [width, setWidth] = useState(0)

  const ref = useCallback((node: HTMLElement | null) => {
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

  return { ref, width }
}
