// features/canvas-core/smart/nodes/useTextModels.test.tsx
// P0-1: the text-model hook fetches the platform llm catalog once, caches it,
// and degrades to an empty list on failure (never throws into render).

import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const listTextModels = vi.fn();
vi.mock('../../services/canvasGenerationService', () => ({
  listTextModels: (...args: unknown[]) => listTextModels(...args),
}));

import { _resetTextModelsCache, useTextModels } from './useTextModels';

beforeEach(() => {
  _resetTextModelsCache();
  listTextModels.mockReset();
});
afterEach(() => {
  _resetTextModelsCache();
});

describe('useTextModels', () => {
  it('returns the fetched catalog rows', async () => {
    const rows = [
      { name: 'mediahub-doubao-llm', display_name: 'Doubao LLM', type: 'llm', actual_provider: 'doubao' },
    ];
    listTextModels.mockResolvedValue(rows);
    const { result } = renderHook(() => useTextModels());
    await waitFor(() => expect(result.current).toEqual(rows));
    expect(listTextModels).toHaveBeenCalledTimes(1);
  });

  it('degrades to an empty list when the fetch fails', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    listTextModels.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useTextModels());
    await waitFor(() => expect(listTextModels).toHaveBeenCalled());
    expect(result.current).toEqual([]);
    spy.mockRestore();
  });
});
