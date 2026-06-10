import { describe, expect, it } from 'vitest';

import {
  MAX_PAD_PER_SIDE,
  ZERO_PADDING,
  hasExtension,
  paddingForAspect,
  setSide,
  targetResolution,
} from './outpaintMath';

describe('setSide / hasExtension', () => {
  it('sets one side immutably and clamps into range', () => {
    const next = setSide(ZERO_PADDING, 'left', 0.5);
    expect(next.left).toBe(0.5);
    expect(ZERO_PADDING.left).toBe(0);
    expect(setSide(ZERO_PADDING, 'top', -1).top).toBe(0);
    expect(setSide(ZERO_PADDING, 'right', 99).right).toBe(MAX_PAD_PER_SIDE);
    expect(setSide(ZERO_PADDING, 'bottom', NaN).bottom).toBe(0);
  });

  it('hasExtension is false only for zero everywhere', () => {
    expect(hasExtension(ZERO_PADDING)).toBe(false);
    expect(hasExtension(setSide(ZERO_PADDING, 'bottom', 0.1))).toBe(true);
  });
});

describe('targetResolution', () => {
  it('mirrors the backend rounding per side', () => {
    const padding = { left: 0.5, top: 0, right: 0.5, bottom: 0 };
    expect(targetResolution(padding, { width: 100, height: 50 })).toEqual({
      width: 200,
      height: 50,
    });
  });

  it('rounds each side independently', () => {
    const padding = { left: 0.333, top: 0, right: 0, bottom: 0.5 };
    expect(targetResolution(padding, { width: 100, height: 100 })).toEqual({
      width: 133,
      height: 150,
    });
  });
});

describe('paddingForAspect', () => {
  it('grows width symmetrically toward a wider aspect', () => {
    // 100x100 → 16:9 needs width 177.78: extra 0.7778 split per side.
    const padding = paddingForAspect(16 / 9, { width: 100, height: 100 });
    expect(padding.left).toBeCloseTo(0.3889, 3);
    expect(padding.right).toBeCloseTo(0.3889, 3);
    expect(padding.top).toBe(0);
    expect(padding.bottom).toBe(0);
  });

  it('grows height symmetrically toward a taller aspect', () => {
    const padding = paddingForAspect(9 / 16, { width: 90, height: 90 });
    expect(padding.left).toBe(0);
    expect(padding.top).toBeCloseTo((160 / 90 - 1) / 2, 3);
    expect(padding.bottom).toBeCloseTo(padding.top, 6);
  });

  it('matching aspect returns zero padding', () => {
    expect(paddingForAspect(1, { width: 50, height: 50 })).toEqual(
      ZERO_PADDING,
    );
  });

  it('unreachable aspect (per-side cap) returns zero padding', () => {
    // 100x10 → 9:16 would need enormous vertical padding.
    expect(paddingForAspect(9 / 16, { width: 100, height: 10 })).toEqual(
      ZERO_PADDING,
    );
  });

  it('degenerate inputs return zero padding', () => {
    expect(paddingForAspect(NaN, { width: 100, height: 100 })).toEqual(
      ZERO_PADDING,
    );
    expect(paddingForAspect(1.5, { width: 0, height: 100 })).toEqual(
      ZERO_PADDING,
    );
  });
});
