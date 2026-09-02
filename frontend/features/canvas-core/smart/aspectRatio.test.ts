// features/canvas-core/smart/aspectRatio.test.ts
//
// The ratio → CSS conversion, covered directly. Going through the node view
// could not reach the numeric guards: a division by zero is the stated reason
// this helper exists, and `'16:0'` takes a different branch from `'wide-ish'`
// (a bad value, not a bad shape).
//
// The invariant that matters: nothing invalid is ever returned. An invalid
// `aspect-ratio` is DROPPED by the browser, so a bad value would look like a
// reserved box in the markup while reserving nothing at all — the collapsing
// cell this helper exists to prevent, now wearing a disguise.

import { describe, expect, it } from 'vitest';

import { FALLBACK_ASPECT, cssAspectRatio, cssAspectRatioOrNull } from './aspectRatio';

describe('cssAspectRatioOrNull', () => {
  it('converts the picker form', () => {
    expect(cssAspectRatioOrNull('16:9')).toBe('16 / 9');
    expect(cssAspectRatioOrNull('3:4')).toBe('3 / 4');
  });

  it('accepts the CSS-native form and surrounding whitespace', () => {
    expect(cssAspectRatioOrNull('16/9')).toBe('16 / 9');
    expect(cssAspectRatioOrNull(' 16 : 9 ')).toBe('16 / 9');
  });

  it.each([
    ['unset', undefined],
    ['null', null],
    ['empty', ''],
    ['whitespace only', '   '],
  ])('is null when the ratio is %s', (_label, value) => {
    expect(cssAspectRatioOrNull(value)).toBeNull();
  });

  it("is null for 'auto', in any casing", () => {
    // 'auto' means "follow the source image", which only the dispatch side
    // can resolve. Reaching here unresolved means genuinely unknown.
    expect(cssAspectRatioOrNull('auto')).toBeNull();
    expect(cssAspectRatioOrNull('AUTO')).toBeNull();
  });

  it.each([
    ['free text', 'wide-ish'],
    ['one term', '16'],
    ['three terms', '16:9:2'],
    ['a non-numeric term', '16:nine'],
  ])('is null for %s', (_label, value) => {
    expect(cssAspectRatioOrNull(value)).toBeNull();
  });

  it.each([
    ['a zero denominator', '16:0'],
    ['a zero numerator', '0:9'],
    ['a negative term', '-16:9'],
    ['Infinity', 'Infinity:9'],
  ])('is null for %s rather than emitting it', (_label, value) => {
    expect(cssAspectRatioOrNull(value)).toBeNull();
  });
});

describe('cssAspectRatio', () => {
  it('agrees with the nullable form whenever a ratio is knowable', () => {
    expect(cssAspectRatio('16:9')).toBe('16 / 9');
  });

  it('is square wherever the nullable form is null', () => {
    for (const value of [undefined, null, '', 'auto', 'wide-ish', '16:0', '-16:9'])
      expect(cssAspectRatio(value)).toBe(FALLBACK_ASPECT);
    expect(FALLBACK_ASPECT).toBe('1 / 1');
  });
});
