import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useMediaQuery } from './useMediaQuery';

function stubMatchMedia(initial: boolean) {
  const listeners = new Set<() => void>();
  const mql = {
    matches: initial,
    media: '',
    onchange: null,
    addEventListener: vi.fn((_: string, fn: () => void) => listeners.add(fn)),
    removeEventListener: vi.fn((_: string, fn: () => void) => listeners.delete(fn)),
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  };
  vi.stubGlobal('matchMedia', (q: string) => Object.assign(mql, { media: q }));
  return {
    mql,
    listeners,
    set(matches: boolean) {
      mql.matches = matches;
      listeners.forEach((fn) => fn());
    },
  };
}

describe('useMediaQuery', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('returns the fallback where matchMedia is unavailable', () => {
    vi.stubGlobal('matchMedia', undefined);
    expect(renderHook(() => useMediaQuery('(min-width: 1px)', true)).result.current).toBe(true);
    expect(renderHook(() => useMediaQuery('(min-width: 1px)', false)).result.current).toBe(false);
  });

  it('reads the current match and follows change events', () => {
    const mm = stubMatchMedia(false);
    const { result } = renderHook(() => useMediaQuery('(prefers-reduced-motion: reduce)', false));
    expect(result.current).toBe(false);
    act(() => mm.set(true));
    expect(result.current).toBe(true);
  });

  it('unsubscribes on unmount', () => {
    const mm = stubMatchMedia(true);
    const { unmount } = renderHook(() => useMediaQuery('(min-width: 640px)', false));
    expect(mm.listeners.size).toBe(1);
    unmount();
    expect(mm.listeners.size).toBe(0);
  });
});
