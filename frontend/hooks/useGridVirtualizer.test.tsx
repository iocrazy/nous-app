import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { useRef } from 'react'
import { useGridVirtualizer } from './useGridVirtualizer'

// Controllable ResizeObserver: capture the observed element's callback so the
// test can drive a width, and record whether observe() was ever called.
let observedWidth = 0
let observeCalls = 0
class FakeResizeObserver {
  private cb: ResizeObserverCallback
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb
  }
  observe(el: Element) {
    observeCalls += 1
    // Fire synchronously like a real observer does on observe().
    this.cb(
      [{ contentRect: { width: observedWidth } } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    )
  }
  unobserve() {}
  disconnect() {}
}

// Harness: mirrors ResourceGrid — the grid container is conditionally rendered,
// so on a cold load it mounts AFTER the first render (items arrive late).
function Harness({ showContainer }: { showContainer: boolean }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const { columns, containerRef } = useGridVirtualizer({
    scrollRef,
    itemCount: 100,
  })
  return (
    <div ref={scrollRef}>
      <span data-testid="columns">{columns}</span>
      {showContainer && <div ref={containerRef} data-testid="grid" />}
    </div>
  )
}

describe('useGridVirtualizer cold-load column measurement', () => {
  beforeEach(() => {
    observedWidth = 1400
    observeCalls = 0
    global.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('measures width and updates columns when the container mounts AFTER first render', () => {
    // Cold load: first render has no items → container absent → fallback columns.
    const { rerender } = render(<Harness showContainer={false} />)
    expect(screen.getByTestId('columns').textContent).toBe('2')

    // Items arrive → container mounts. The callback ref must (re)attach the
    // observer NOW; a `useRef`+`[]` effect would have missed this and left
    // columns stuck at the 2-column fallback (the stretched-grid regression).
    act(() => {
      rerender(<Harness showContainer />)
    })

    // observe() must have fired at least once now that the node is attached.
    expect(observeCalls).toBeGreaterThanOrEqual(1)
    // width 1400 → columnsForWidth = min(8, floor(1400/172)) = 8.
    expect(screen.getByTestId('columns').textContent).toBe('8')
  })

  it('measures a wide container present from the first render', () => {
    observedWidth = 1720 // floor(1720/172) = 10 → capped at 8
    render(<Harness showContainer />)
    expect(observeCalls).toBeGreaterThanOrEqual(1)
    expect(screen.getByTestId('columns').textContent).toBe('8')
  })
})
