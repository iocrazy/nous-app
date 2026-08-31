// features/canvas-core/smart/nodes/useModelCapabilities.test.ts
/**
 * Capabilities cache: the UI's licence to hide a knob.
 *
 * `null` is a deliberate value, not an error state: it means "capabilities
 * unknown" (old backend, fetch failed), and every consumer must render FULL
 * support in that case — hiding a knob on missing data would break working
 * setups on the day an old backend serves a new frontend.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const listGenerationCapabilities = vi.fn();
vi.mock('../../services/canvasGenerationService', async (orig) => ({
  ...(await orig<object>()),
  listGenerationCapabilities: () => listGenerationCapabilities(),
}));

import {
  _resetModelCapabilitiesCache,
  useModelCapabilities,
} from './useModelCapabilities';

const CAPS = {
  'codex-local-image': {
    ratios: ['1:1', '16:9'], quality: false, resolution: false,
    max_refs: 9, negative: false, video_modes: [],
  },
};

beforeEach(() => {
  _resetModelCapabilitiesCache();
  listGenerationCapabilities.mockReset();
});
afterEach(() => {
  _resetModelCapabilitiesCache();
});

describe('useModelCapabilities', () => {
  it('returns the caps for a known model', async () => {
    listGenerationCapabilities.mockResolvedValue(CAPS);
    const { result } = renderHook(() => useModelCapabilities('codex-local-image'));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current!.quality).toBe(false);
    // Same loaded map, a name it does not carry: unknown, so callers render
    // FULL support rather than treating "absent" as "unsupported".
    const other = renderHook(() => useModelCapabilities('not-in-the-catalog'));
    expect(other.result.current).toBeNull();
  });

  it('returns null while loading and on fetch failure', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    listGenerationCapabilities.mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useModelCapabilities('codex-local-image'));
    expect(result.current).toBeNull();
    await waitFor(() => expect(listGenerationCapabilities).toHaveBeenCalled());
    expect(result.current, 'failure must read as "unknown", never as "supports nothing"').toBeNull();
    spy.mockRestore();
  });

  it('fetches once per session (module cache), like useGenerationModels', async () => {
    listGenerationCapabilities.mockResolvedValue(CAPS);
    const a = renderHook(() => useModelCapabilities('codex-local-image'));
    await waitFor(() => expect(a.result.current).not.toBeNull());
    renderHook(() => useModelCapabilities('codex-local-image'));
    expect(listGenerationCapabilities).toHaveBeenCalledTimes(1);
  });

  it('does not retry after a failure — one attempt per session', async () => {
    // The only structural difference from useGenerationModels is the `settled`
    // gate. Without an assertion here, swapping it back for a `cache` check
    // would turn nothing red.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    listGenerationCapabilities.mockRejectedValue(new Error('offline'));
    const a = renderHook(() => useModelCapabilities('codex-local-image'));
    await waitFor(() => expect(spy).toHaveBeenCalled()); // the catch ran: settled
    a.unmount();

    const b = renderHook(() => useModelCapabilities('codex-local-image'));
    await new Promise((r) => setTimeout(r, 0)); // let any second attempt settle
    expect(b.result.current).toBeNull();
    expect(listGenerationCapabilities).toHaveBeenCalledTimes(1);
    expect(spy, 'a settled failure is neither re-attempted nor re-logged').toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });

  it('picks up a cache that settled elsewhere when its model name arrives late', async () => {
    // The node that mounts before the catalog loads: no model name yet, so it
    // never subscribes to the fetch. Another node settles the module cache. If
    // this one does not re-sync when its name arrives, capability-hiding is a
    // silent no-op for it forever (null renders as full support).
    listGenerationCapabilities.mockResolvedValue(CAPS);
    const late = renderHook(({ m }: { m: string }) => useModelCapabilities(m), {
      initialProps: { m: '' },
    });
    expect(late.result.current).toBeNull();

    const seed = renderHook(() => useModelCapabilities('codex-local-image'));
    await waitFor(() => expect(seed.result.current).not.toBeNull());

    late.rerender({ m: 'codex-local-image' });
    await waitFor(() => expect(late.result.current).not.toBeNull());
    expect(late.result.current!.max_refs).toBe(9);
  });

  it('returns null for an empty/absent model name', () => {
    const { result } = renderHook(() => useModelCapabilities(''));
    expect(result.current).toBeNull();
  });
});
