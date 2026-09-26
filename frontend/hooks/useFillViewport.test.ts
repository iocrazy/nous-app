import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useFillViewport } from './useFillViewport'

function elAt(top: number, bottom = top + 1): HTMLElement {
  const el = document.createElement('div')
  el.getBoundingClientRect = () => ({ top, bottom } as DOMRect)
  return el
}

describe('useFillViewport', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0)
      return 1
    })
    vi.stubGlobal('cancelAnimationFrame', () => {})
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('loads the next page while the sentinel is still on screen', () => {
    const loadMore = vi.fn()
    renderHook(() =>
      useFillViewport({
        sentinelRef: { current: elAt(600) },
        scrollRef: { current: elAt(0, 880) },
        enabled: true,
        contentKey: 20,
        loadMore,
      }),
    )
    expect(loadMore).toHaveBeenCalledTimes(1)
  })

  it('stops once the content runs past the bottom of the scroller', () => {
    const loadMore = vi.fn()
    renderHook(() =>
      useFillViewport({
        sentinelRef: { current: elAt(1400) },
        scrollRef: { current: elAt(0, 880) },
        enabled: true,
        contentKey: 40,
        loadMore,
      }),
    )
    expect(loadMore).not.toHaveBeenCalled()
  })

  it('does not load while disabled', () => {
    const loadMore = vi.fn()
    renderHook(() =>
      useFillViewport({
        sentinelRef: { current: elAt(600) },
        scrollRef: { current: elAt(0, 880) },
        enabled: false,
        contentKey: 20,
        loadMore,
      }),
    )
    expect(loadMore).not.toHaveBeenCalled()
  })

  it('does not retry the same content length (a failed load must not loop)', () => {
    const loadMore = vi.fn()
    const props = {
      sentinelRef: { current: elAt(600) },
      scrollRef: { current: elAt(0, 880) },
      contentKey: 20,
      loadMore,
    }
    const { rerender } = renderHook((p: { enabled: boolean }) =>
      useFillViewport({ ...props, enabled: p.enabled }),
    { initialProps: { enabled: true } })
    rerender({ enabled: false }) // load in flight
    rerender({ enabled: true }) // load failed, length unchanged
    expect(loadMore).toHaveBeenCalledTimes(1)
  })
})
