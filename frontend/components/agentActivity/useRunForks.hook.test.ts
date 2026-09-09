/**
 * useRunForks (harness 2b-1 §2): one read per settled run for the page life,
 * a poll while the run is live, no state after unmount.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getRunForks = vi.fn();
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: { getRunForks: (...a: unknown[]) => getRunForks(...a) },
}));
const { useRunForks, __clearRunForksCache } = await import('./useRunForks');

beforeEach(() => {
  __clearRunForksCache();
  getRunForks.mockReset().mockResolvedValue({ items: [{ run_id: '701', at_seq: 4, created_at: '', status: 'completed' }] });
});
afterEach(() => vi.useRealTimers());

describe('useRunForks', () => {
  it('reads a settled run once and serves remounts from the cache', async () => {
    const a = renderHook(() => useRunForks('r1', false));
    await waitFor(() => expect(a.result.current).toHaveLength(1));
    a.unmount();
    const b = renderHook(() => useRunForks('r1', false));
    expect(b.result.current).toHaveLength(1);
    expect(getRunForks).toHaveBeenCalledTimes(1);
  });

  it('polls while the run is live and stops on unmount', async () => {
    vi.useFakeTimers();
    const { result, unmount } = renderHook(() => useRunForks('r2', true));
    await vi.advanceTimersByTimeAsync(0);
    expect(getRunForks).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(getRunForks).toHaveBeenCalledTimes(2);
    unmount();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(getRunForks).toHaveBeenCalledTimes(2);
    expect(result.current).toHaveLength(1);
  });

  it('a null run id yields nothing and no request', () => {
    const { result } = renderHook(() => useRunForks(null, false));
    expect(result.current).toEqual([]);
    expect(getRunForks).not.toHaveBeenCalled();
  });
});
