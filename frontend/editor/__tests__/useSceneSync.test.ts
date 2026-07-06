/**
 * Behaviour tests for useSceneSync — the optimistic op queue (spec §3.4 D4/D5).
 * Each of the six behaviour rules gets at least one independent test, plus the
 * strict-serial-queue guarantee and the 409-identical silent replay.
 *
 * sceneService is mocked so applyOps/getScene are controllable, but the real
 * VersionConflictError/OpRejectedError classes are kept (useSceneSync switches
 * on `instanceof`, so the thrown instance must be the genuine class).
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { useSceneSync } from '../useSceneSync';
import { OpRejectedError, VersionConflictError } from '../sceneService';
import type { ScriptElement, SceneDoc } from '../types';

vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

vi.mock('../sceneService', async () => {
  const actual = await vi.importActual<typeof import('../sceneService')>('../sceneService');
  return { ...actual, applyOps: vi.fn(), getScene: vi.fn() };
});

// Bound after the mock is registered.
import { applyOps, getScene } from '../sceneService';
const mockApplyOps = applyOps as unknown as Mock;
const mockGetScene = getScene as unknown as Mock;

function el(id: string, text: string, type: ScriptElement['type'] = 'action'): ScriptElement {
  return { id, type, text, character_id: null };
}

function makeScene(overrides: Partial<SceneDoc> = {}): SceneDoc {
  return {
    id: 's1',
    script_id: 'sc1',
    chapter_id: null,
    heading_int_ext: null,
    location_text: null,
    time_of_day: null,
    content_version: 1,
    elements: [el('el_00000001', 'hello')],
    sort_order: 0,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Let the pump's promise chain settle (a handful of microtask hops). */
async function drain() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  mockApplyOps.mockReset();
  mockGetScene.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('rule 1 — optimistic view + strict serial flush', () => {
  it('shows optimistic elements immediately and sets saving', () => {
    mockApplyOps.mockReturnValue(deferred<never>().promise);
    const { result } = renderHook(() => useSceneSync(makeScene()));

    const next = [el('el_00000001', 'hello'), el('el_00000002', 'world')];
    act(() => {
      result.current.dispatchOps(
        [{ op: 'insert', element_id: 'el_00000002', after_id: 'el_00000001', payload: { type: 'action', text: 'world' } }],
        next,
      );
    });

    expect(result.current.elements).toEqual(next);
    expect(result.current.saveState).toBe('saving');
  });

  it('never runs two applyOps in flight — three dispatches stay serial', async () => {
    const gates = [deferred<{ content_version: number; elements: ScriptElement[] }>(), deferred<{ content_version: number; elements: ScriptElement[] }>(), deferred<{ content_version: number; elements: ScriptElement[] }>()];
    let call = 0;
    mockApplyOps.mockImplementation(() => gates[call++].promise);

    const { result } = renderHook(() => useSceneSync(makeScene()));

    act(() => {
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'a' } }], [el('el_00000001', 'a')]);
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'b' } }], [el('el_00000001', 'b')]);
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'c' } }], [el('el_00000001', 'c')]);
    });

    // Only the first batch is in flight.
    expect(mockApplyOps).toHaveBeenCalledTimes(1);
    expect(mockApplyOps.mock.calls[0][2]).toBe(1); // expectedVersion = initial

    await act(async () => {
      gates[0].resolve({ content_version: 2, elements: [el('el_00000001', 'a')] });
    });
    await drain();
    expect(mockApplyOps).toHaveBeenCalledTimes(2);
    expect(mockApplyOps.mock.calls[1][2]).toBe(2); // advanced version

    await act(async () => {
      gates[1].resolve({ content_version: 3, elements: [el('el_00000001', 'b')] });
    });
    await drain();
    expect(mockApplyOps).toHaveBeenCalledTimes(3);

    await act(async () => {
      gates[2].resolve({ content_version: 4, elements: [el('el_00000001', 'c')] });
    });
    await drain();
    expect(result.current.version).toBe(4);
    expect(result.current.saveState).toBe('saved');
  });
});

describe('rule 2 — success advances version and settles to saved', () => {
  it('updates version and drains to saved', async () => {
    mockApplyOps.mockResolvedValue({ content_version: 2, elements: [el('el_00000001', 'x')] });
    const { result } = renderHook(() => useSceneSync(makeScene()));

    act(() => {
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'x' } }], [el('el_00000001', 'x')]);
    });
    await drain();

    expect(result.current.version).toBe(2);
    expect(result.current.saveState).toBe('saved');
    expect(result.current.conflict).toBeNull();
  });
});

describe('rule 3 — 409 whose server state equals optimistic is silent', () => {
  it('adopts the version, replays the queue, and never surfaces a conflict', async () => {
    const optimisticA = [el('el_00000001', 'A')];
    const optimisticB = [el('el_00000001', 'B')];
    // First batch: server already holds exactly our optimistic result → 409-identical.
    mockApplyOps.mockRejectedValueOnce(new VersionConflictError(5, optimisticA));
    // Second batch replays on top of the adopted version and succeeds.
    mockApplyOps.mockResolvedValueOnce({ content_version: 6, elements: optimisticB });

    const { result } = renderHook(() => useSceneSync(makeScene()));

    act(() => {
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'A' } }], optimisticA);
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'B' } }], optimisticB);
    });
    await drain();

    expect(result.current.conflict).toBeNull();
    expect(result.current.saveState).toBe('saved');
    expect(result.current.version).toBe(6);
    // Second call was sent with the adopted version 5.
    expect(mockApplyOps.mock.calls[1][2]).toBe(5);
  });
});

describe('rule 4 — 409 with divergent content raises a conflict', () => {
  const theirs = [el('el_00000001', 'their text')];
  const mine = [el('el_00000001', 'mine text')];

  it('freezes the queue and exposes conflict{mine,theirs}', async () => {
    mockApplyOps.mockRejectedValueOnce(new VersionConflictError(9, theirs));
    const { result } = renderHook(() => useSceneSync(makeScene()));

    act(() => {
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'mine text' } }], mine);
    });
    await drain();

    expect(result.current.saveState).toBe('conflict');
    expect(result.current.conflict).toEqual({ mine, theirs });
    expect(mockApplyOps).toHaveBeenCalledTimes(1); // queue frozen — no further sends
  });

  it("resolveConflict('mine') replays mine as upserts on the server version", async () => {
    mockApplyOps.mockRejectedValueOnce(new VersionConflictError(9, theirs));
    mockApplyOps.mockResolvedValueOnce({ content_version: 10, elements: mine });
    const { result } = renderHook(() => useSceneSync(makeScene()));

    act(() => {
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'mine text' } }], mine);
    });
    await drain();

    act(() => result.current.resolveConflict('mine'));
    await drain();

    expect(mockApplyOps).toHaveBeenCalledTimes(2);
    const [, replayOps, expectedVersion] = mockApplyOps.mock.calls[1];
    expect(expectedVersion).toBe(9); // rebuilt on top of theirs
    expect(replayOps).toEqual([
      { op: 'insert', element_id: 'el_00000001', after_id: null, payload: { type: 'action', text: 'mine text', character_id: null } },
    ]);
    expect(result.current.saveState).toBe('saved');
    expect(result.current.version).toBe(10);
    expect(result.current.conflict).toBeNull();
  });

  it("resolveConflict('theirs') drops pending work and adopts the server", async () => {
    mockApplyOps.mockRejectedValueOnce(new VersionConflictError(9, theirs));
    const { result } = renderHook(() => useSceneSync(makeScene()));

    act(() => {
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'mine text' } }], mine);
    });
    await drain();

    act(() => result.current.resolveConflict('theirs'));
    await drain();

    expect(mockApplyOps).toHaveBeenCalledTimes(1); // nothing re-sent
    expect(result.current.elements).toEqual(theirs);
    expect(result.current.version).toBe(9);
    expect(result.current.saveState).toBe('saved');
    expect(result.current.conflict).toBeNull();
  });
});

describe('rule 5 — network errors retry with backoff / go offline', () => {
  it('retries the same batch with 1s/2s/4s exponential backoff', async () => {
    vi.useFakeTimers();
    try {
      // Reject three times (network), then succeed.
      mockApplyOps
        .mockRejectedValueOnce(new Error('network'))
        .mockRejectedValueOnce(new Error('network'))
        .mockRejectedValueOnce(new Error('network'))
        .mockResolvedValueOnce({ content_version: 2, elements: [el('el_00000001', 'x')] });

      const { result } = renderHook(() => useSceneSync(makeScene()));
      act(() => {
        result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'x' } }], [el('el_00000001', 'x')]);
      });

      await act(async () => {
        await Promise.resolve();
      });
      expect(result.current.saveState).toBe('retrying');
      expect(mockApplyOps).toHaveBeenCalledTimes(1);

      // 1s → retry #2
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(mockApplyOps).toHaveBeenCalledTimes(2);

      // 2s → retry #3
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(mockApplyOps).toHaveBeenCalledTimes(3);

      // 4s → retry #4 (succeeds)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });
      expect(mockApplyOps).toHaveBeenCalledTimes(4);

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(result.current.saveState).toBe('saved');
      expect(result.current.version).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('goes offline when navigator.onLine is false and resumes on the online event', async () => {
    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    mockApplyOps.mockRejectedValueOnce(new Error('network'));

    const { result } = renderHook(() => useSceneSync(makeScene()));
    act(() => {
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'x' } }], [el('el_00000001', 'x')]);
    });
    await drain();
    expect(result.current.saveState).toBe('offline');

    // Back online: the queued batch flushes.
    onLine.mockReturnValue(true);
    mockApplyOps.mockResolvedValueOnce({ content_version: 2, elements: [el('el_00000001', 'x')] });
    await act(async () => {
      window.dispatchEvent(new Event('online'));
    });
    await drain();

    expect(result.current.saveState).toBe('saved');
    expect(result.current.version).toBe(2);
  });
});

describe('rule 6 — 422 is logged, dropped, and the scene is refetched', () => {
  it('never swallows OpRejectedError; refetches the scene as truth', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockApplyOps.mockRejectedValueOnce(new OpRejectedError('missing_anchor', { at: 'el_x' }));
    const serverTruth = makeScene({ content_version: 3, elements: [el('el_00000001', 'server')] });
    mockGetScene.mockResolvedValueOnce(serverTruth);

    const { result } = renderHook(() => useSceneSync(makeScene()));
    act(() => {
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'bad' } }], [el('el_00000001', 'bad')]);
    });
    await drain();

    expect(errorSpy).toHaveBeenCalled();
    expect(errorSpy.mock.calls[0][0]).toContain('422');
    expect(mockGetScene).toHaveBeenCalledWith('s1');
    expect(result.current.elements).toEqual(serverTruth.elements);
    expect(result.current.version).toBe(3);
    expect(result.current.saveState).toBe('saved');
  });
});

describe('flush()', () => {
  it('resolves only once the queue has drained', async () => {
    const gate = deferred<{ content_version: number; elements: ScriptElement[] }>();
    mockApplyOps.mockReturnValueOnce(gate.promise);

    const { result } = renderHook(() => useSceneSync(makeScene()));
    act(() => {
      result.current.dispatchOps([{ op: 'update', element_id: 'el_00000001', payload: { text: 'x' } }], [el('el_00000001', 'x')]);
    });

    let resolved = false;
    let flushPromise!: Promise<void>;
    act(() => {
      flushPromise = result.current.flush().then(() => {
        resolved = true;
      });
    });
    await drain();
    expect(resolved).toBe(false); // still in flight

    await act(async () => {
      gate.resolve({ content_version: 2, elements: [el('el_00000001', 'x')] });
    });
    await drain();
    await flushPromise;
    expect(resolved).toBe(true);
  });

  it('releases waiters when syncing halts on a conflict (guard re-checks saveState)', async () => {
    const theirs = [el('el_00000001', 'their text')];
    mockApplyOps.mockRejectedValueOnce(new VersionConflictError(9, theirs));

    const { result } = renderHook(() => useSceneSync(makeScene()));
    let resolved = false;
    await act(async () => {
      result.current.dispatchOps(
        [{ op: 'update', element_id: 'el_00000001', payload: { text: 'mine' } }],
        [el('el_00000001', 'mine')],
      );
      void result.current.flush().then(() => {
        resolved = true;
      });
    });
    await drain();
    // flush() must NOT hang on unbounded user interaction — it resolves and
    // the caller sees saveState==='conflict' to decide to prompt.
    expect(resolved).toBe(true);
    expect(result.current.saveState).toBe('conflict');
  });

  it('resolves immediately when nothing is queued', async () => {
    const { result } = renderHook(() => useSceneSync(makeScene()));
    await expect(result.current.flush()).resolves.toBeUndefined();
  });
});
