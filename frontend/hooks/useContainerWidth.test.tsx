/**
 * useContainerWidth — the width measurement all three grids now share.
 *
 * Extracted in fix round 1 and, until this file, exercised only indirectly:
 * useGridVirtualizer's tests drive the observer path, and the DownloadsView
 * adaptive test mocks the hook out entirely. The synchronous attach-time read
 * — the thing that keeps a cold load from painting one frame at the fallback
 * width — had no coverage at all.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { useContainerWidth } from './useContainerWidth'

let observeCalls = 0
let disconnectCalls = 0
let lastCallback: ResizeObserverCallback | null = null

// Deliberately does NOT invoke the callback from observe(): a real
// ResizeObserver delivers its first notification asynchronously, after the
// effect has run. Firing it synchronously here would mask the attach-time
// measurement entirely, which is the thing under test.
class FakeResizeObserver {
  constructor(cb: ResizeObserverCallback) {
    lastCallback = cb
  }
  observe() {
    observeCalls += 1
  }
  unobserve() {}
  disconnect() {
    disconnectCalls += 1
  }
}

/** Deliver a resize notification the way the browser would, post-effect. */
function emitResize(width: number) {
  act(() => {
    lastCallback?.([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver)
  })
}

/** Harness mirroring real usage: the container mounts conditionally. */
function Harness({ showContainer, rectWidth }: { showContainer: boolean; rectWidth?: number }) {
  const { ref, width } = useContainerWidth()
  return (
    <div>
      <span data-testid="width">{width}</span>
      {showContainer && (
        <div
          data-testid="box"
          ref={(node) => {
            if (node && rectWidth !== undefined) {
              node.getBoundingClientRect = () =>
                ({ width: rectWidth }) as DOMRect
            }
            ref(node)
          }}
        />
      )}
    </div>
  )
}

describe('useContainerWidth', () => {
  beforeEach(() => {
    observeCalls = 0
    disconnectCalls = 0
    lastCallback = null
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('reports 0 before any container attaches', () => {
    render(<Harness showContainer={false} />)
    expect(screen.getByTestId('width').textContent).toBe('0')
    expect(observeCalls).toBe(0)
  })

  it('measures synchronously on attach, before any observer notification', () => {
    // No callback has been delivered yet, so a non-zero width can ONLY have
    // come from the getBoundingClientRect read at attach time. That read is
    // what keeps a cold load from painting one frame at the fallback width.
    render(<Harness showContainer rectWidth={900} />)
    expect(screen.getByTestId('width').textContent).toBe('900')
    expect(observeCalls).toBeGreaterThanOrEqual(1)
  })

  it('ignores a zero rect rather than committing a bogus 0', () => {
    render(<Harness showContainer rectWidth={0} />)
    expect(screen.getByTestId('width').textContent).toBe('0')
    // The real width arrives with the first notification.
    emitResize(640)
    expect(screen.getByTestId('width').textContent).toBe('640')
  })

  it('attaches the observer when the container mounts AFTER first render', () => {
    const { rerender } = render(<Harness showContainer={false} />)
    expect(observeCalls).toBe(0)

    act(() => {
      rerender(<Harness showContainer rectWidth={1200} />)
    })

    expect(observeCalls).toBeGreaterThanOrEqual(1)
    expect(screen.getByTestId('width').textContent).toBe('1200')
  })

  it('tracks later resizes through the observer callback', () => {
    render(<Harness showContainer rectWidth={800} />)
    expect(screen.getByTestId('width').textContent).toBe('800')

    emitResize(480)
    expect(screen.getByTestId('width').textContent).toBe('480')
  })

  it('disconnects the observer on unmount', () => {
    const { unmount } = render(<Harness showContainer rectWidth={700} />)
    expect(disconnectCalls).toBe(0)
    unmount()
    expect(disconnectCalls).toBe(1)
  })

  it('still measures once, and says so, when ResizeObserver is unavailable', () => {
    // Old browsers and jsdom. The first layout must still be correct; only
    // reflow is lost, and that is reported rather than swallowed.
    vi.stubGlobal('ResizeObserver', undefined)
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(() => render(<Harness showContainer rectWidth={1024} />)).not.toThrow()
    expect(screen.getByTestId('width').textContent).toBe('1024')
    expect(errSpy).toHaveBeenCalled()
  })
})
