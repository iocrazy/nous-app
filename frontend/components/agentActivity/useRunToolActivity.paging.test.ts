/**
 * harness 2b-1 §1: the transcript read pages with after_seq until has_more
 * is false — a 500-event page cap must not silently truncate a run.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const getRunEvents = vi.fn();
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: { getRunEvents: (...a: unknown[]) => getRunEvents(...a) },
}));

const { useRunToolActivity, __clearRunToolActivityCache } = await import('./useRunToolActivity');

const ev = (seq: number) => ({ seq, event_type: 'step_start', payload: { turn: 1, step: seq }, created_at: '' });

afterEach(() => {
  __clearRunToolActivityCache();
  getRunEvents.mockReset();
});

describe('useRunToolActivity — paging', () => {
  it('follows has_more with after_seq and concatenates the pages', async () => {
    getRunEvents
      .mockResolvedValueOnce({ items: [ev(1), ev(2)], count: 2, has_more: true })
      .mockResolvedValueOnce({ items: [ev(3)], count: 1, has_more: true })
      .mockResolvedValueOnce({ items: [], count: 0, has_more: false });
    const { result } = renderHook(() => useRunToolActivity('r1', false));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(getRunEvents.mock.calls.map((c) => c[1])).toEqual([0, 2, 3]);
  });

  it('a single page without has_more is one call (pre-2b-1 shape still works)', async () => {
    getRunEvents.mockResolvedValueOnce({ items: [ev(1)], count: 1 });
    const { result } = renderHook(() => useRunToolActivity('r2', false));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(getRunEvents).toHaveBeenCalledTimes(1);
    expect(result.current.events.map((e) => e.seq)).toEqual([1]);
  });
});
