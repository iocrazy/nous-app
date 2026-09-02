import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { useRef } from 'react'
import { useGridVirtualizer } from './useGridVirtualizer'
import { cardWidthFor } from '../utils/gridColumns'

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
    // width 1400 → floor((1400+12)/172) = 8 columns of ~164px.
    expect(screen.getByTestId('columns').textContent).toBe('8')
  })

  it('measures a wide container present from the first render', () => {
    // width 1720 → floor((1720+12)/172) = 10 columns of ~161px.
    //
    // This used to assert 8, pinning the old `Math.min(8, ...)` column cap. The
    // cap is gone: a fixed column ceiling and a max-card-width band are
    // contradictory (at 2400px an 8-column grid means 289px cards, well past
    // the 220px cap), and the band is what makes the layout size-stable when
    // the info panel opens. 10 columns here also matches what the sibling
    // `.downloads-grid` CSS rule — auto-fill minmax(160px, 220px) — produces
    // at the same width, so the virtualized grid and the folder grid above it
    // no longer disagree about card size.
    observedWidth = 1720
    render(<Harness showContainer />)
    expect(observeCalls).toBeGreaterThanOrEqual(1)
    expect(screen.getByTestId('columns').textContent).toBe('10')
  })
})

/**
 * The reflow regression, pinned AT THE LAYER THE BUG LIVED IN.
 *
 * The defect was never in `columnsForWidth`. It was the `isMobile` short-circuit
 * INSIDE this hook — `const isMobile = width > 0 && width < 768` — computed from
 * the CONTAINER width the ResizeObserver reports, not the viewport. A pure-helper
 * test cannot see it: re-adding that line here leaves `columnsForWidth` band-based
 * and all of its unit tests still green.
 *
 * WHERE THE CLIFF ACTUALLY IS — 768px of CONTAINER, which is not the viewport.
 * The grid container is the viewport minus the 224px resources sidebar
 * (`ResourcesSidebar.tsx:185`, `w-52` + a `w-4` rail) minus the info panel
 * (default 320, user-resizable). So a 1280px window gives ~1056 with the panel
 * closed and ~736 with it open — and 736 is under the cliff:
 *
 *   container 769+ → old rule 4 columns of ~183px   (old and new agree)
 *   container 767- → old rule 2 columns of ~378px   (2.06x jump)
 *
 * That discontinuity is the bug. Note it does NOT reproduce at every width: at
 * 780 the old rule already returned 4 columns, so a 780px case cannot catch the
 * mutation. The sub-768 cases below are the ones that can, and the 1340/780 pair
 * is kept only as a plain size-stability check.
 *
 * Mutation-checked: re-inserting `width < 768 → 2` turns the three sub-768
 * assertions red (see the report for the recorded failure count).
 */
describe('useGridVirtualizer — container narrowing must not resize cards', () => {
  beforeEach(() => {
    observeCalls = 0
    global.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  /** Render at one observed container width and read the column count back. */
  function columnsAt(width: number): number {
    observedWidth = width
    const view = render(<Harness showContainer />)
    const columns = Number(screen.getByTestId('columns').textContent)
    view.unmount()
    return columns
  }

  it('does not collapse to 2 giant columns just below the old 768 cliff', () => {
    // One pixel under the old threshold: the worst case of the discontinuity.
    expect(columnsAt(767)).toBe(4)
    expect(cardWidthFor(767, columnsAt(767))).toBeLessThan(220)
  })

  it('drops columns rather than inflating cards when the panel opens', () => {
    // A 1280px window: ~1056 of container with the info panel closed, ~736 with
    // it open. This is the real reported scenario.
    const closed = columnsAt(1056)
    const open = columnsAt(736)

    expect(closed).toBe(6)
    expect(open).toBe(4)

    // Card width is what the user perceives, so assert on that, not just counts.
    const closedCard = cardWidthFor(1056, closed)
    const openCard = cardWidthFor(736, open)
    expect(Math.abs(openCard - closedCard) / closedCard).toBeLessThan(0.15)
    // Under the old rule this was 2 columns of 362px — pin that it cannot return.
    expect(openCard).toBeLessThan(220)
  })

  it('keeps 2 columns at a genuine phone container width', () => {
    // The band rule reaches 2 on its own here, which is why the hook needs no
    // mobile branch — that branch only ever mislabelled narrow desktop panes.
    expect(columnsAt(375)).toBe(2)
  })

  it('stays size-stable across the brief\'s 1340 to 780 pair', () => {
    // Both sit above the old cliff, so this pair passes on the OLD code too. It
    // is a stability check, not a regression guard — the guards are above.
    const wide = columnsAt(1340)
    const narrow = columnsAt(780)
    expect(wide).toBe(7)
    expect(narrow).toBe(4)

    const wideCard = cardWidthFor(1340, wide)
    const narrowCard = cardWidthFor(780, narrow)
    expect(Math.abs(narrowCard - wideCard) / wideCard).toBeLessThan(0.15)
  })
})
