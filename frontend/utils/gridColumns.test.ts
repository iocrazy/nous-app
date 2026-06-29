import { describe, it, expect } from 'vitest'
import { columnsForWidth } from './gridColumns'

describe('columnsForWidth', () => {
  it('returns 2 for width=0 (safe default before first measure)', () => {
    expect(columnsForWidth(0, false)).toBe(2)
  })

  it('returns 2 for mobile width regardless of calculation', () => {
    expect(columnsForWidth(375, true)).toBe(2)
  })

  it('returns 4 for width=800 on desktop', () => {
    // Math.floor(800 / 172) = Math.floor(4.65) = 4
    expect(columnsForWidth(800, false)).toBe(4)
  })

  it('clamps to 8 for very wide screens', () => {
    // Math.floor(2000 / 172) = Math.floor(11.6) = 11 → clamped to 8
    expect(columnsForWidth(2000, false)).toBe(8)
  })

  it('returns 2 (min clamp) for narrow-but-not-zero width', () => {
    // Math.floor(300 / 172) = Math.floor(1.74) = 1 → clamped to 2
    expect(columnsForWidth(300, false)).toBe(2)
  })
})
