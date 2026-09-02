import { describe, it, expect } from 'vitest'
import {
  columnsForWidth,
  cardWidthFor,
  MIN_CARD_WIDTH,
  MAX_CARD_WIDTH,
} from './gridColumns'

describe('columnsForWidth', () => {
  it('returns 2 for width=0 (safe default before first measure)', () => {
    expect(columnsForWidth(0)).toBe(2)
  })

  it('returns 2 for a non-finite width', () => {
    expect(columnsForWidth(Number.NaN)).toBe(2)
  })

  it('yields 2 columns at a phone container width', () => {
    // 375px: floor((375+12)/172) = 2. The old rule special-cased "mobile" to
    // reach the same answer; the band rule gets there on its own.
    expect(columnsForWidth(375)).toBe(2)
  })

  it('never lets a card exceed the max-width cap', () => {
    for (let w = 120; w <= 3000; w += 7) {
      const cols = columnsForWidth(w)
      expect(cardWidthFor(w, cols)).toBeLessThanOrEqual(MAX_CARD_WIDTH + 0.001)
    }
  })

  it('keeps cards at or above the min width from the documented floor up', () => {
    // 504px is where the min-width and max-width bounds stop conflicting; the
    // doc comment on MIN_CARD_WIDTH explains why they conflict below it.
    for (let w = 504; w <= 3000; w += 7) {
      const cols = columnsForWidth(w)
      expect(cardWidthFor(w, cols)).toBeGreaterThanOrEqual(MIN_CARD_WIDTH)
    }
  })

  it('pins 504 as the exact floor of that guarantee', () => {
    // Falsifiable both ways, so moving MIN/MAX/GAP without updating the comment
    // turns this red rather than silently drifting the documented number.
    expect(cardWidthFor(504, columnsForWidth(504))).toBeGreaterThanOrEqual(MIN_CARD_WIDTH)
    expect(cardWidthFor(503, columnsForWidth(503))).toBeLessThan(MIN_CARD_WIDTH)
  })

  it('adds columns monotonically as the container widens', () => {
    let prev = 0
    for (let w = 200; w <= 3000; w += 13) {
      const cols = columnsForWidth(w)
      expect(cols).toBeGreaterThanOrEqual(prev)
      prev = cols
    }
  })
})

describe('reflow stability — info panel opening must not resize cards', () => {
  // The reported bug: clicking a resource opens the 320px info panel, the grid
  // container goes 1340 → ~780, and the grid used to collapse to 2 columns of
  // ~384px cards (2.1x bigger). Card width must now stay put; only the column
  // count drops.
  it('keeps card width within +/-15% from 1340px to 780px', () => {
    const wide = cardWidthFor(1340, columnsForWidth(1340))
    const narrow = cardWidthFor(780, columnsForWidth(780))

    expect(columnsForWidth(1340)).toBeGreaterThan(columnsForWidth(780))
    expect(Math.abs(narrow - wide) / wide).toBeLessThan(0.15)
  })

  it('keeps card width inside the design band across every panel-open step', () => {
    // Sweep the desktop range against a 320px panel opening.
    //
    // Note on the bound: a tighter-than-band guarantee is NOT achievable with a
    // discrete column count. Going from n to n+1 columns changes card width by
    // n/(n+1) — 25% at the 3->4 boundary — so no column rule can hold +/-15% at
    // every width. What IS guaranteed is that both layouts stay inside the
    // [MIN, MAX] card band, i.e. the worst case is bounded by 220/160 = 1.375x
    // rather than the 2.1x blow-up the old rule produced.
    for (let full = 900; full <= 2400; full += 20) {
      const withPanel = full - 320
      const a = cardWidthFor(full, columnsForWidth(full))
      const b = cardWidthFor(withPanel, columnsForWidth(withPanel))

      for (const cardWidth of [a, b]) {
        expect(cardWidth).toBeLessThanOrEqual(MAX_CARD_WIDTH + 0.001)
        expect(cardWidth).toBeGreaterThanOrEqual(MIN_CARD_WIDTH - 0.001)
      }
      expect(Math.abs(b - a) / a).toBeLessThan(MAX_CARD_WIDTH / MIN_CARD_WIDTH - 1)
    }
  })

  it('regression: 780px does NOT produce a 2-column giant-card layout', () => {
    expect(columnsForWidth(780)).toBeGreaterThan(2)
    expect(cardWidthFor(780, columnsForWidth(780))).toBeLessThan(250)
  })
})

describe('narrow containers — below the app\'s real range, pinned deliberately', () => {
  // The old rule had a hard 2-column minimum. The band rule drops that: nothing
  // in the app reaches these widths (the narrowest real container is a 375px
  // phone, which still yields 2), but the behaviour should be intentional
  // rather than incidental, so it is pinned either way.
  it('gives a single column where a second would breach the min width', () => {
    expect(columnsForWidth(172)).toBe(1)
    expect(columnsForWidth(200)).toBe(1)
    expect(columnsForWidth(220)).toBe(1)
  })

  it('adds the second column as soon as the cap requires it', () => {
    // 221 is the boundary: a lone card there would exceed MAX_CARD_WIDTH, so
    // the widening pass fires and splits it in two.
    expect(columnsForWidth(221)).toBe(2)
    expect(cardWidthFor(221, 2)).toBeLessThanOrEqual(MAX_CARD_WIDTH)
  })

  it('still reports 2 for the unmeasured width, so nothing renders 1-up on a cold load', () => {
    expect(columnsForWidth(0)).toBe(2)
  })
})
