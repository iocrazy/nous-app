import { beforeEach, describe, expect, it } from 'vitest';
import {
  RELOAD_THROTTLE_MS,
  STALE_CHUNK_RELOADED_KEY,
  isStaleChunkError,
  shouldReloadForStaleChunk,
} from './staleChunkReload';

/**
 * 2026-07-26: "主页出现了，但点击不进去".
 *
 * Lazy routes reject with "Failed to fetch dynamically imported module" after a
 * redeploy replaces the content-hashed chunks an already-open tab still refers
 * to. `lazyWithRetry` reloads once to self-heal — but it gated that on a
 * write-only sessionStorage sentinel:
 *
 *     const reloaded = sessionStorage.getItem(KEY);
 *     if (!reloaded) { setItem(KEY, String(Date.now())); reload(); }
 *     throw err;   // 2nd time onwards
 *
 * It stored a timestamp and never read it, and never cleared the key. So one
 * tab got exactly ONE self-heal for its whole lifetime; after a second deploy
 * every lazy route threw and the detail page became unreachable (the home page
 * kept working because its component was already in memory).
 *
 * Fix: throttle by time instead of once-per-tab. A fresh deploy therefore gets
 * a fresh reload budget, while a genuinely broken chunk (offline, 500) can't
 * loop because retries inside the window are refused.
 */

class FakeStorage {
  private map = new Map<string, string>();
  getItem(k: string) {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string) {
    this.map.set(k, v);
  }
  removeItem(k: string) {
    this.map.delete(k);
  }
}

describe('isStaleChunkError', () => {
  it.each([
    'Failed to fetch dynamically imported module: https://app.nous.ink/assets/x-A1b2.js',
    'Loading chunk 42 failed',
    'error loading dynamically imported module',
  ])('recognises %s', (msg) => {
    expect(isStaleChunkError(msg)).toBe(true);
  });

  it.each([
    'NetworkError when attempting to fetch resource',
    "Cannot read properties of undefined (reading 'map')",
    '',
  ])('does not misfire on %s', (msg) => {
    expect(isStaleChunkError(msg)).toBe(false);
  });
});

describe('shouldReloadForStaleChunk', () => {
  let storage: FakeStorage;
  beforeEach(() => {
    storage = new FakeStorage();
  });

  it('reloads on the first stale chunk', () => {
    expect(shouldReloadForStaleChunk(storage, 1_000_000)).toBe(true);
  });

  it('records the reload time so the window can be evaluated later', () => {
    shouldReloadForStaleChunk(storage, 1_000_000);
    expect(storage.getItem(STALE_CHUNK_RELOADED_KEY)).toBe('1000000');
  });

  it('refuses a second reload inside the throttle window (loop guard)', () => {
    shouldReloadForStaleChunk(storage, 1_000_000);
    expect(
      shouldReloadForStaleChunk(storage, 1_000_000 + RELOAD_THROTTLE_MS - 1),
    ).toBe(false);
  });

  it('allows another reload once the window has passed — this is the bug', () => {
    // A later deploy replaces chunks again; the tab must be able to self-heal
    // a second time instead of throwing forever.
    shouldReloadForStaleChunk(storage, 1_000_000);
    expect(
      shouldReloadForStaleChunk(storage, 1_000_000 + RELOAD_THROTTLE_MS + 1),
    ).toBe(true);
  });

  it('survives a corrupted sentinel by treating it as "never reloaded"', () => {
    storage.setItem(STALE_CHUNK_RELOADED_KEY, 'not-a-number');
    expect(shouldReloadForStaleChunk(storage, 1_000_000)).toBe(true);
  });

  it('is not fooled by a sentinel from the future', () => {
    // Clock skew / manual tampering must not disable self-healing forever.
    storage.setItem(STALE_CHUNK_RELOADED_KEY, String(9_000_000));
    expect(shouldReloadForStaleChunk(storage, 1_000_000)).toBe(true);
  });
});
