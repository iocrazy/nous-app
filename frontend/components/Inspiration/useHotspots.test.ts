import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

const getHotspots = vi.fn();
const setHotspotState = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspots: (...a: unknown[]) => getHotspots(...a),
  setHotspotState: (...a: unknown[]) => setHotspotState(...a),
}));

import { useHotspots } from './useHotspots';

const HS = (id: string, over = {}) => ({ id, title: `t${id}`, tags: [], ...over });

describe('useHotspots', () => {
  beforeEach(() => {
    getHotspots.mockReset();
    setHotspotState.mockReset();
    getHotspots.mockResolvedValue([HS('1'), HS('2')]);
  });

  it('does not fetch when disabled', () => {
    renderHook(() => useHotspots({ enabled: false }));
    expect(getHotspots).not.toHaveBeenCalled();
  });

  it('fetches for the given day when enabled', async () => {
    const { result } = renderHook(() => useHotspots({ enabled: true, day: '2026-07-07' }));
    await waitFor(() => expect(result.current.hotspots).toHaveLength(2));
    expect(getHotspots).toHaveBeenCalledWith('2026-07-07', undefined, undefined, 'all', undefined, undefined);
  });

  it('threads tagIds into getHotspots as the 6th arg', async () => {
    const { result } = renderHook(() =>
      useHotspots({ enabled: true, day: '2026-07-07', tagIds: ['a', 'b'] }),
    );
    await waitFor(() => expect(result.current.hotspots).toHaveLength(2));
    expect(getHotspots).toHaveBeenCalledWith('2026-07-07', undefined, undefined, 'all', undefined, [
      'a',
      'b',
    ]);
  });

  it('passes undefined tag filter when tagIds is empty', async () => {
    const { result } = renderHook(() => useHotspots({ enabled: true, tagIds: [] }));
    await waitFor(() => expect(result.current.hotspots).toHaveLength(2));
    expect(getHotspots).toHaveBeenCalledWith(undefined, undefined, undefined, 'all', undefined, undefined);
  });

  it('refetches when tagIds change', async () => {
    const { rerender } = renderHook(({ ids }) => useHotspots({ enabled: true, tagIds: ids }), {
      initialProps: { ids: ['a'] as string[] },
    });
    await waitFor(() => expect(getHotspots).toHaveBeenCalledTimes(1));
    rerender({ ids: ['a', 'b'] });
    await waitFor(() => expect(getHotspots).toHaveBeenCalledTimes(2));
    expect(getHotspots).toHaveBeenLastCalledWith(undefined, undefined, undefined, 'all', undefined, [
      'a',
      'b',
    ]);
  });

  it('does not refetch when tagIds keeps the same values', async () => {
    const { rerender } = renderHook(({ ids }) => useHotspots({ enabled: true, tagIds: ids }), {
      initialProps: { ids: ['a', 'b'] as string[] },
    });
    await waitFor(() => expect(getHotspots).toHaveBeenCalledTimes(1));
    rerender({ ids: ['a', 'b'] }); // new array identity, same values
    // stable-key deps must not trigger an identity-churn refetch
    await new Promise((r) => setTimeout(r, 20));
    expect(getHotspots).toHaveBeenCalledTimes(1);
  });

  it('applyState optimistically patches then persists', async () => {
    setHotspotState.mockResolvedValue({});
    const { result } = renderHook(() => useHotspots({ enabled: true }));
    await waitFor(() => expect(result.current.hotspots).toHaveLength(2));
    await act(async () => {
      await result.current.applyState(HS('1'), { is_saved: true });
    });
    expect(setHotspotState).toHaveBeenCalledWith('1', { is_saved: true });
    expect(result.current.hotspots.find((h) => h.id === '1')?.is_saved).toBe(true);
  });

  it('applyState reloads to roll back on failure', async () => {
    setHotspotState.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useHotspots({ enabled: true }));
    await waitFor(() => expect(result.current.hotspots).toHaveLength(2));
    getHotspots.mockClear();
    await act(async () => {
      await result.current.applyState(HS('1'), { is_saved: true }).catch(() => {});
    });
    await waitFor(() => expect(getHotspots).toHaveBeenCalled());
  });

  it('surfaces load errors without throwing', async () => {
    getHotspots.mockRejectedValue(new Error('down'));
    const { result } = renderHook(() => useHotspots({ enabled: true }));
    await waitFor(() => expect(result.current.error).toBe('down'));
    expect(result.current.hotspots).toEqual([]);
  });
});
