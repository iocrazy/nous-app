import { useCallback, useEffect, useState } from 'react'

/**
 * Measures a container's layout width via ResizeObserver.
 *
 * `ref` is a CALLBACK ref, not a RefObject, on purpose: grid containers here
 * are conditionally rendered (they mount only once there are items), so on a
 * cold load the node attaches AFTER the first render. A `useEffect(..., [])`
 * reading a RefObject would run once at mount, find null, never attach the
 * observer, and leave the width pinned at 0 — which the uniform grid reads as
 * "not measured yet" and renders as a permanent 2-column layout. Driving the
 * effect off callback-ref state re-runs it the moment the node attaches.
 *
 * The width is also read synchronously on attach so the first painted frame
 * already has the real number; the observer callback lands a frame later.
 *
 * Shared by all three grids — the resource grid's uniform virtualizer, its
 * justified virtualizer, and the downloads grid — so they cannot drift on this.
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

    // Environments without ResizeObserver (jsdom, very old browsers) still get
    // the synchronous measurement above — a correct first layout that simply
    // never reflows. Reported rather than swallowed, because "the grid stopped
    // responding to resizes" is otherwise indistinguishable from a layout bug.
    if (typeof ResizeObserver === 'undefined') {
      console.error(
        'ResizeObserver unavailable: grid width measured once at attach and will not track resizes.',
      )
      return
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setWidth(entry.contentRect.width)
    })
    observer.observe(containerEl)
    return () => observer.disconnect()
  }, [containerEl])

  return { ref, width }
}
