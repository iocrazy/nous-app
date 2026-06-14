import { describe, expect, it } from 'vitest';

import { elapsedSeconds, formatElapsed } from './elapsed';

describe('formatElapsed', () => {
  it('formats sub-minute as Ns', () => {
    expect(formatElapsed(0)).toBe('0s');
    expect(formatElapsed(12)).toBe('12s');
    expect(formatElapsed(59)).toBe('59s');
  });

  it('formats >= 60s as Nm SSs with zero-padded seconds', () => {
    expect(formatElapsed(60)).toBe('1m 00s');
    expect(formatElapsed(63)).toBe('1m 03s');
    expect(formatElapsed(125)).toBe('2m 05s');
  });

  it('clamps negatives and floors fractions', () => {
    expect(formatElapsed(-5)).toBe('0s');
    expect(formatElapsed(12.9)).toBe('12s');
  });
});

describe('elapsedSeconds', () => {
  it('returns whole seconds between start and now', () => {
    const start = '2026-06-14T00:00:00.000Z';
    const now = new Date('2026-06-14T00:00:07.500Z').getTime();
    expect(elapsedSeconds(start, now)).toBe(7);
  });

  it('returns 0 for null/invalid start or a now before start', () => {
    expect(elapsedSeconds(null, Date.now())).toBe(0);
    expect(elapsedSeconds('not-a-date', Date.now())).toBe(0);
    const start = '2026-06-14T00:00:10.000Z';
    expect(elapsedSeconds(start, new Date('2026-06-14T00:00:05.000Z').getTime())).toBe(0);
  });
});
