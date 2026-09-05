// features/canvas-core/library/usePromptCatalog.test.tsx
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const fetchPrompts = vi.fn();
const fetchPromptCounts = vi.fn();
vi.mock('../../../services/promptsService', async (orig) => ({ ...(await orig<typeof import('../../../services/promptsService')>()), fetchPrompts: (...a: unknown[]) => fetchPrompts(...a), fetchPromptCounts: (...a: unknown[]) => fetchPromptCounts(...a) }));
import { usePromptCatalog } from './usePromptCatalog';

const page = { items: [], total: 0, by_form: { template: 0, image: 0, album: 0 }, by_origin: { typed: 0, extracted: 0, captioned: 0 } };

describe('usePromptCatalog', () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); fetchPrompts.mockReset().mockResolvedValue(page); fetchPromptCounts.mockReset().mockResolvedValue({ mine: 1, project: null, system: 0 }); });
  afterEach(() => vi.useRealTimers());

  it('fetches page and counts, debouncing the query', async () => {
    const { result, rerender } = renderHook((p: { query: string }) => usePromptCatalog({ scopeId: '9000', segment: 'mine', projectId: null, form: null, query: p.query, enabled: true }), { initialProps: { query: '' } });
    await waitFor(() => expect(result.current.page).toEqual(page));
    expect(fetchPromptCounts).toHaveBeenCalledTimes(1);
    rerender({ query: 'r' }); rerender({ query: 'ra' });
    act(() => { vi.advanceTimersByTime(299); });
    expect(fetchPrompts).toHaveBeenCalledTimes(1);
    act(() => { vi.advanceTimersByTime(1); });
    await waitFor(() => expect(fetchPrompts).toHaveBeenLastCalledWith('9000', expect.objectContaining({ q: 'ra' })));
  });

  it('does nothing while disabled and surfaces errors', async () => {
    const { result, rerender } = renderHook((p: { enabled: boolean }) => usePromptCatalog({ scopeId: '9000', segment: 'mine', projectId: null, form: null, query: '', enabled: p.enabled }), { initialProps: { enabled: false } });
    expect(fetchPrompts).not.toHaveBeenCalled();
    fetchPrompts.mockRejectedValueOnce(new Error('boom'));
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.error?.message).toBe('boom'));
  });
});
