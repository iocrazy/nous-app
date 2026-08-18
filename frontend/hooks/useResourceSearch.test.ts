import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useResourceSearch } from './useResourceSearch';
import * as svc from '../services/resourceSearchService';

describe('useResourceSearch', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('debounces 150ms before firing', async () => {
    const spy = vi.spyOn(svc, 'searchResources').mockResolvedValue({
      results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    });
    const { rerender } = renderHook(({ q }) => useResourceSearch(q, ''), {
      initialProps: { q: '' },
    });
    rerender({ q: 's' });
    rerender({ q: 'st' });
    rerender({ q: 'sto' });
    expect(spy).not.toHaveBeenCalled();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1), { timeout: 400 });
    expect(spy).toHaveBeenLastCalledWith({ q: 'sto', kinds: '', limit: 50, teamId: undefined, signal: expect.anything() });
  });

  it('aborts in-flight when query changes', async () => {
    const ctrls: AbortController[] = [];
    vi.spyOn(svc, 'searchResources').mockImplementation(async ({ signal }) => {
      ctrls.push({ signal } as any);
      return new Promise(() => {});
    });
    const { rerender } = renderHook(({ q }) => useResourceSearch(q, ''), { initialProps: { q: 's' } });
    await waitFor(() => expect(svc.searchResources).toHaveBeenCalledTimes(1));
    rerender({ q: 'st' });
    await waitFor(() => expect(svc.searchResources).toHaveBeenCalledTimes(2));
    expect(ctrls[0].signal.aborted).toBe(true);
  });

  it('passes teamId through to searchResources', async () => {
    const spy = vi.spyOn(svc, 'searchResources').mockResolvedValue({
      results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }, next_cursor: null,
    });
    renderHook(() => useResourceSearch('story', '', 'team-900'));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(expect.objectContaining({ teamId: 'team-900' })),
    );
  });

  it('degrades a malformed response to the empty shape instead of crashing consumers', async () => {
    // Wrong envelope (e.g. a generic {success,data} body or proxy HTML parsed
    // to junk). Consumers read `data.results.length` unconditionally — a
    // shapeless object here white-screens the whole canvas page.
    const spy = vi
      .spyOn(svc, 'searchResources')
      .mockResolvedValue({ success: true, data: [] } as never);
    const { result } = renderHook(() => useResourceSearch('hero', ''));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(Array.isArray(result.current.data.results)).toBe(true);
    expect(result.current.data.results).toHaveLength(0);
    expect(result.current.data.counts.all).toBe(0);
  });
});
