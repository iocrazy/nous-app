// features/canvas-core/smart/nodes/staleCatalog.test.tsx
// The canvas model catalogs are module-cached, but a cache that lives for the
// whole page session hides probe changes: a nous-engine model that became
// loaded (idle → ok) stayed greyed until a full reload. The cache now goes
// stale after 10 minutes and refetches when the window regains focus.

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const listTextModels = vi.fn();
const listGenerationModels = vi.fn();
vi.mock('../../services/canvasGenerationService', () => ({
  listTextModels: (...a: unknown[]) => listTextModels(...a),
  listGenerationModels: (...a: unknown[]) => listGenerationModels(...a),
}));

import { _resetTextModelsCache, useTextModels } from './useTextModels';
import { _resetGenerationModelsCache, useGenerationModels } from './useGenerationModels';

const IDLE = [{ name: 'nous-qwen3-8-27b', type: 'llm', last_test_status: 'idle' }];
const OK = [{ name: 'nous-qwen3-8-27b', type: 'llm', last_test_status: 'ok' }];
const T0 = new Date('2026-09-24T10:00:00Z').getTime();

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(T0);
  _resetTextModelsCache();
  _resetGenerationModelsCache();
  listTextModels.mockReset();
  listGenerationModels.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
  _resetTextModelsCache();
  _resetGenerationModelsCache();
});

async function mountText() {
  const hook = renderHook(() => useTextModels());
  await waitFor(() => expect(hook.result.current.length).toBeGreaterThan(0));
  return hook;
}

describe('canvas model catalog staleness', () => {
  it('a fresh cache is reused by the next mount — no refetch', async () => {
    listTextModels.mockResolvedValue(IDLE);
    (await mountText()).unmount();
    vi.setSystemTime(T0 + 9 * 60_000);
    const again = renderHook(() => useTextModels());
    expect(again.result.current).toEqual(IDLE);
    expect(listTextModels).toHaveBeenCalledTimes(1);
  });

  it('a cache older than 10 minutes is refetched on the next mount', async () => {
    listTextModels.mockResolvedValueOnce(IDLE).mockResolvedValueOnce(OK);
    (await mountText()).unmount();
    vi.setSystemTime(T0 + 10 * 60_000 + 1);
    const again = renderHook(() => useTextModels());
    // Stale rows show immediately; the refresh replaces them.
    expect(again.result.current).toEqual(IDLE);
    await waitFor(() => expect(again.result.current).toEqual(OK));
    expect(listTextModels).toHaveBeenCalledTimes(2);
  });

  it('regaining window focus refetches and updates mounted pickers', async () => {
    listTextModels.mockResolvedValueOnce(IDLE).mockResolvedValueOnce(OK);
    const hook = await mountText();
    vi.setSystemTime(T0 + 60_000);
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() => expect(hook.result.current).toEqual(OK));
    expect(listTextModels).toHaveBeenCalledTimes(2);
  });

  it('focus right after a fetch does not refetch (no alt-tab storm)', async () => {
    listTextModels.mockResolvedValue(IDLE);
    await mountText();
    vi.setSystemTime(T0 + 5_000);
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    expect(listTextModels).toHaveBeenCalledTimes(1);
  });

  it('a failed refresh keeps the rows already shown', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    listTextModels.mockResolvedValueOnce(IDLE).mockRejectedValueOnce(new Error('boom'));
    const hook = await mountText();
    vi.setSystemTime(T0 + 60_000);
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() => expect(listTextModels).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(hook.result.current).toEqual(IDLE);
    spy.mockRestore();
  });

  it('the generation catalog follows the same rule', async () => {
    const img = (s: string) => [{ name: 'nous-studio-image', type: 'image', last_test_status: s }];
    listGenerationModels.mockResolvedValueOnce(img('idle')).mockResolvedValueOnce(img('ok'));
    const hook = renderHook(() => useGenerationModels('image'));
    await waitFor(() => expect(hook.result.current).toEqual(img('idle')));
    vi.setSystemTime(T0 + 60_000);
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() => expect(hook.result.current).toEqual(img('ok')));
  });
});
