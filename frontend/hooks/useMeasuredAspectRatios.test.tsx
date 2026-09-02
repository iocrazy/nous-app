import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { useMeasuredAspectRatios } from './useMeasuredAspectRatios'

// Manually-pumped rAF so the test controls exactly when a frame boundary
// happens — the whole point of the hook is what lands per frame.
let frameQueue: FrameRequestCallback[] = []

function pumpFrame() {
  const queued = frameQueue
  frameQueue = []
  act(() => {
    queued.forEach((cb) => cb(performance.now()))
  })
}

let renderCount = 0
let latest: ReturnType<typeof useMeasuredAspectRatios> | null = null

function Harness() {
  renderCount += 1
  latest = useMeasuredAspectRatios()
  return <span data-testid="json">{JSON.stringify(latest.measured)}</span>
}

describe('useMeasuredAspectRatios', () => {
  beforeEach(() => {
    frameQueue = []
    renderCount = 0
    latest = null
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      frameQueue.push(cb)
      return frameQueue.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts empty', () => {
    render(<Harness />)
    expect(screen.getByTestId('json').textContent).toBe('{}')
  })

  it('does not update state before the frame boundary', () => {
    render(<Harness />)
    act(() => {
      latest!.report('a', 1.5)
    })
    // Reported but not flushed: the layout must not have moved yet.
    expect(screen.getByTestId('json').textContent).toBe('{}')

    pumpFrame()
    expect(JSON.parse(screen.getByTestId('json').textContent!)).toEqual({ a: 1.5 })
  })

  it('coalesces a burst of reports into ONE re-render', () => {
    render(<Harness />)
    const before = renderCount

    // A screenful of thumbnails resolving at once.
    act(() => {
      for (let i = 0; i < 30; i += 1) latest!.report(`id-${i}`, 1 + i / 100)
    })
    expect(renderCount).toBe(before) // nothing yet

    pumpFrame()

    // Exactly one commit for all 30, not 30 commits.
    expect(renderCount).toBe(before + 1)
    expect(Object.keys(JSON.parse(screen.getByTestId('json').textContent!))).toHaveLength(30)
  })

  it('schedules only one frame for a burst', () => {
    render(<Harness />)
    act(() => {
      latest!.report('a', 1)
      latest!.report('b', 2)
      latest!.report('c', 3)
    })
    expect(frameQueue).toHaveLength(1)
  })

  it('ignores a repeat report of an unchanged value', () => {
    render(<Harness />)
    act(() => { latest!.report('a', 1.5) })
    pumpFrame()
    const after = renderCount

    act(() => { latest!.report('a', 1.5) })
    // Nothing staged, so no frame is even scheduled.
    expect(frameQueue).toHaveLength(0)
    pumpFrame()
    expect(renderCount).toBe(after)
  })

  it('accepts a corrected value for an id already measured', () => {
    render(<Harness />)
    act(() => { latest!.report('a', 1.5) })
    pumpFrame()

    act(() => { latest!.report('a', 0.5) })
    pumpFrame()
    expect(JSON.parse(screen.getByTestId('json').textContent!)).toEqual({ a: 0.5 })
  })

  it('drops nonsense ratios and empty ids', () => {
    render(<Harness />)
    act(() => {
      latest!.report('a', 0)
      latest!.report('b', -3)
      latest!.report('c', Number.NaN)
      latest!.report('d', Number.POSITIVE_INFINITY)
      latest!.report('', 1.5)
    })
    expect(frameQueue).toHaveLength(0)
    pumpFrame()
    expect(screen.getByTestId('json').textContent).toBe('{}')
  })

  it('does not set state after unmount', () => {
    const { unmount } = render(<Harness />)
    act(() => { latest!.report('a', 1.5) })
    unmount()
    // Flushing a queued frame post-unmount must be a no-op, not a warning.
    expect(() => pumpFrame()).not.toThrow()
  })
})
