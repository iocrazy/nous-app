import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { useSelectedVideoTags } from './useDownloadsData';
import type { Tag } from '../../types';

const tagA: Tag = {
  id: 't1', name: 'Anime', color: '#fff', icon: null, type: 'user',
  created_at: '2026-01-01T00:00:00Z',
};
const tagB: Tag = {
  id: 't2', name: 'AI', color: '#6366f1', icon: null, type: 'user',
  created_at: '2026-01-01T00:00:00Z',
};

const fetchResourceTags = vi.fn();
vi.mock('../../services/resourceService', () => ({
  fetchResourceTags: (...a: unknown[]) => fetchResourceTags(...a),
  addResourceTag: vi.fn(),
  removeResourceTag: vi.fn(),
}));

describe('useSelectedVideoTags — R1 allTags sync', () => {
  beforeEach(() => {
    fetchResourceTags.mockReset();
  });

  it('refetchTags appends a newly-assigned tag missing from allTags', async () => {
    fetchResourceTags.mockResolvedValue([{ tag: tagA }]); // initial mount load
    const { result } = renderHook(() => useSelectedVideoTags('r1'));
    await waitFor(() => expect(result.current.selectedVideoTags).toEqual([{ tag: tagA }]));

    let allTags: Tag[] = [tagA];
    const setAllTags = vi.fn((updater: (prev: Tag[]) => Tag[]) => {
      allTags = updater(allTags);
    });
    const { result: result2 } = renderHook(() => useSelectedVideoTags('r1', setAllTags));
    await waitFor(() => expect(result2.current.selectedVideoTags).toEqual([{ tag: tagA }]));

    fetchResourceTags.mockResolvedValue([{ tag: tagA }, { tag: tagB }]);
    await act(async () => {
      await result2.current.refetchTags();
    });

    expect(allTags.map((t) => t.id)).toEqual(['t1', 't2']);
  });

  it('is a no-op when setAllTags is omitted (backward compatible)', async () => {
    fetchResourceTags.mockResolvedValue([{ tag: tagA }]);
    const { result } = renderHook(() => useSelectedVideoTags('r1'));
    await waitFor(() => expect(result.current.selectedVideoTags).toEqual([{ tag: tagA }]));

    fetchResourceTags.mockResolvedValue([{ tag: tagA }, { tag: tagB }]);
    await act(async () => {
      await result.current.refetchTags();
    });

    expect(result.current.selectedVideoTags).toEqual([{ tag: tagA }, { tag: tagB }]);
  });
});
