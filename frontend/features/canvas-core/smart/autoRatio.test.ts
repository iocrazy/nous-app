// Picking the generation ratio from the input image.
//
// Reported: "the default resolution should follow the main image unless I
// change it." Every prompt was seeded with a hard-coded `ratio: '1:1'`, so a
// 16:9 source produced a square unless the user noticed and set it by hand.
//
// `'auto'` is a real ratio value meaning "follow the source", resolved to a
// concrete ratio at dispatch. The video side already ships the same idea as
// IC's 自适应; this brings images in line.

import { describe, expect, it, vi } from 'vitest';

import { MEASURE_TIMEOUT_MS, isAutoRatio, measureRatio, nearestRatio } from './autoRatio';

describe('nearestRatio', () => {
  it('maps an exact 16:9 source to 16:9', () => {
    expect(nearestRatio(1920, 1080)).toBe('16:9');
  });

  it('maps a square source to 1:1', () => {
    expect(nearestRatio(1024, 1024)).toBe('1:1');
  });

  it('maps a portrait phone frame to 9:16', () => {
    expect(nearestRatio(1080, 1920)).toBe('9:16');
  });

  it('snaps an imperfect source to the closest offered ratio', () => {
    // 1086×1448 is 0.75 — the real output from a codex portrait request.
    expect(nearestRatio(1086, 1448)).toBe('3:4');
  });

  it('prefers the nearer of two neighbours rather than the first match', () => {
    // 1.6 sits between 3:2 (1.5) and 16:9 (1.78); 1.6 is closer to 1.5.
    expect(nearestRatio(1600, 1000)).toBe('3:2');
  });

  it('returns null for a degenerate size instead of guessing', () => {
    expect(nearestRatio(0, 0)).toBeNull();
    expect(nearestRatio(100, 0)).toBeNull();
  });

  it('only ever returns a ratio the picker actually offers', () => {
    const offered = new Set(['1:1', '2:3', '3:2', '3:4', '4:3', '9:16', '16:9', '21:9']);
    for (const [w, h] of [
      [1920, 1080],
      [1000, 1000],
      [800, 1200],
      [1234, 567],
      [3, 7],
    ]) {
      const r = nearestRatio(w, h);
      expect(r, `${w}x${h} produced ${r}`).not.toBeNull();
      expect(offered.has(r!), `${r} is not an offered ratio`).toBe(true);
    }
  });
});

describe('isAutoRatio', () => {
  it('recognises the auto sentinel', () => {
    expect(isAutoRatio('auto')).toBe(true);
  });

  it('treats a concrete ratio as an explicit user choice', () => {
    expect(isAutoRatio('16:9')).toBe(false);
  });

  it('treats a missing value as auto — an unset prompt should follow its source', () => {
    expect(isAutoRatio(undefined)).toBe(true);
    expect(isAutoRatio(null)).toBe(true);
  });
});

describe('measureRatio — never hangs the dispatch', () => {
  it('gives up after the timeout when the image neither loads nor errors', async () => {
    // jsdom fires neither event, which is also what a dead URL does in a real
    // browser. Without a bound the generation request is never sent at all —
    // the run silently never starts.
    vi.useFakeTimers();
    const pending = measureRatio('https://example.invalid/never.png');
    await vi.advanceTimersByTimeAsync(MEASURE_TIMEOUT_MS + 50);
    await expect(pending).resolves.toBeNull();
    vi.useRealTimers();
  });
});
