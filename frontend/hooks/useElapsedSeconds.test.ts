import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useElapsedSeconds } from './useElapsedSeconds';

describe('useElapsedSeconds', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-26T12:00:00Z'));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns 0 immediately when startedAt is now', () => {
    const { result } = renderHook(() =>
      useElapsedSeconds('2026-05-26T12:00:00Z', { enabled: true }),
    );
    expect(result.current).toBe(0);
  });

  it('increments each second when enabled', () => {
    const { result } = renderHook(() =>
      useElapsedSeconds('2026-05-26T12:00:00Z', { enabled: true }),
    );
    act(() => { vi.advanceTimersByTime(3500); });
    expect(result.current).toBe(3);
  });

  it('freezes when disabled', () => {
    const { result, rerender } = renderHook(
      ({ enabled }) => useElapsedSeconds('2026-05-26T12:00:00Z', { enabled }),
      { initialProps: { enabled: true } },
    );
    act(() => { vi.advanceTimersByTime(2000); });
    expect(result.current).toBe(2);
    rerender({ enabled: false });
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current).toBe(2); // frozen — no longer ticks
  });

  it('returns 0 for null startedAt', () => {
    const { result } = renderHook(() => useElapsedSeconds(null, { enabled: true }));
    expect(result.current).toBe(0);
  });

  it('returns 0 for malformed startedAt', () => {
    const { result } = renderHook(() => useElapsedSeconds('not-a-date', { enabled: true }));
    expect(result.current).toBe(0);
  });

  it('handles startedAt in the past correctly', () => {
    const { result } = renderHook(() =>
      useElapsedSeconds('2026-05-26T11:59:55Z', { enabled: true }),
    );
    expect(result.current).toBe(5);
  });
});
