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
    expect(getHotspots).toHaveBeenCalledWith('2026-07-07', undefined, undefined, 'all', undefined);
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
