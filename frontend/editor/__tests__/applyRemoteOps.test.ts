/**
 * Exhaustive tests for useSceneSync.applyRemoteOps — the C2 remote-op merge
 * (Phase B P5). Covers the three guards: self-echo/stale drop, contiguous clean
 * splice, and gap/dirty reconcile (clean adopt vs. conflict divergence).
 *
 * sceneService.getScene is mocked so the reconcile refetch is controllable;
 * applyOps is mocked so a pending local queue can be held open for the dirty
 * path. The real error classes are kept (unused here but the module needs them).
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { useSceneSync, type RemoteOpRow } from '../useSceneSync';
import type { ScriptElement, SceneDoc } from '../types';

vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));
vi.mock('../sceneService', async () => {
  const actual = await vi.importActual<typeof import('../sceneService')>('../sceneService');
  return { ...actual, applyOps: vi.fn(), getScene: vi.fn() };
});

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

function remoteRow(over: Partial<RemoteOpRow> = {}): RemoteOpRow {
  return {
    scene_id: 's1',
    op_seq: 2,
    actor: 'other-user',
    op_json: { ops: [{ op: 'update', element_id: 'el_00000001', payload: { text: 'remote' } }] },
    ...over,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function drain() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  mockApplyOps.mockReset();
  mockGetScene.mockReset();
});
afterEach(() => vi.restoreAllMocks());

// ---------------------------------------------------------------------------
// Guard 1 — self-echo / stale
// ---------------------------------------------------------------------------
describe('guard 1 — self-echo and stale rows are ignored', () => {
  it('drops a row whose actor is the local user (self-echo)', () => {
    const { result } = renderHook(() => useSceneSync(makeScene(), { selfActorId: 'me' }));
    act(() => result.current.applyRemoteOps(remoteRow({ actor: 'me', op_seq: 2 })));
    expect(result.current.elements).toEqual([el('el_00000001', 'hello')]);
    expect(result.current.version).toBe(1);
    expect(mockGetScene).not.toHaveBeenCalled();
  });

  it('drops a row whose op_seq is <= the current version (already applied)', () => {
    const { result } = renderHook(() => useSceneSync(makeScene({ content_version: 3 })));
    act(() => result.current.applyRemoteOps(remoteRow({ op_seq: 3 })));
    expect(result.current.version).toBe(3);
    expect(result.current.elements).toEqual([el('el_00000001', 'hello')]);
    expect(mockGetScene).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Guard 2 — contiguous clean splice
// ---------------------------------------------------------------------------
describe('guard 2 — contiguous op on clean state applies in place', () => {
  it('splices the forward ops and advances the version (no refetch)', () => {
    const { result } = renderHook(() => useSceneSync(makeScene({ content_version: 1 })));
    act(() =>
      result.current.applyRemoteOps(
        remoteRow({
          op_seq: 2,
          op_json: { ops: [{ op: 'update', element_id: 'el_00000001', payload: { text: 'remote' } }] },
        }),
      ),
    );
    expect(result.current.elements).toEqual([el('el_00000001', 'remote')]);
    expect(result.current.version).toBe(2);
    expect(result.current.saveState).toBe('saved');
    expect(mockGetScene).not.toHaveBeenCalled();
  });

  it('applies an insert op onto the optimistic view', () => {
    const { result } = renderHook(() => useSceneSync(makeScene({ content_version: 1 })));
    act(() =>
      result.current.applyRemoteOps(
        remoteRow({
          op_seq: 2,
          op_json: {
            ops: [
              {
                op: 'insert',
                element_id: 'el_00000002',
                after_id: 'el_00000001',
                payload: { type: 'action', text: 'added' },
              },
            ],
          },
        }),
      ),
    );
    expect(result.current.elements.map((e) => e.id)).toEqual(['el_00000001', 'el_00000002']);
    expect(result.current.version).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Guard 3 — gap / dirty reconcile
// ---------------------------------------------------------------------------
describe('guard 3 — gap on clean state refetches and adopts', () => {
  it('a non-contiguous seq refetches the scene and adopts the server truth', async () => {
    const fresh = makeScene({ content_version: 5, elements: [el('el_00000009', 'server')] });
    mockGetScene.mockResolvedValue(fresh);

    const { result } = renderHook(() => useSceneSync(makeScene({ content_version: 1 })));
    act(() => result.current.applyRemoteOps(remoteRow({ op_seq: 5 })));
    await drain();

    expect(mockGetScene).toHaveBeenCalledTimes(1);
    expect(result.current.version).toBe(5);
    expect(result.current.elements).toEqual([el('el_00000009', 'server')]);
    expect(result.current.saveState).toBe('saved');
    expect(result.current.conflict).toBeNull();
  });

  it('a malformed row (no ops array) reconciles from the server', async () => {
    mockGetScene.mockResolvedValue(makeScene({ content_version: 2, elements: [el('x', 'srv')] }));
    const { result } = renderHook(() => useSceneSync(makeScene({ content_version: 1 })));
    act(() => result.current.applyRemoteOps(remoteRow({ op_seq: 2, op_json: { inverse: [] } })));
    await drain();
    expect(mockGetScene).toHaveBeenCalledTimes(1);
    expect(result.current.version).toBe(2);
  });
});

describe('guard 3 — dirty local state diverges into conflict', () => {
  it('a remote op while a local write is pending surfaces a conflict (never clobbers)', async () => {
    // Hold the local write open so the queue stays non-empty (dirty).
    mockApplyOps.mockReturnValue(deferred<never>().promise);
    const fresh = makeScene({ content_version: 4, elements: [el('el_00000001', 'theirs')] });
    mockGetScene.mockResolvedValue(fresh);

    const { result } = renderHook(() => useSceneSync(makeScene({ content_version: 1 })));

    const mine = [el('el_00000001', 'mine')];
    act(() =>
      result.current.dispatchOps(
        [{ op: 'update', element_id: 'el_00000001', payload: { text: 'mine' } }],
        mine,
      ),
    );

    act(() => result.current.applyRemoteOps(remoteRow({ op_seq: 3 })));
    await drain();

    expect(mockGetScene).toHaveBeenCalledTimes(1);
    expect(result.current.saveState).toBe('conflict');
    expect(result.current.conflict).toEqual({
      mine,
      theirs: [el('el_00000001', 'theirs')],
    });
    // Our optimistic elements are untouched — no clobber.
    expect(result.current.elements).toEqual(mine);
  });

  it('resolving the remote-triggered conflict with "theirs" adopts the server view', async () => {
    mockApplyOps.mockReturnValue(deferred<never>().promise);
    const fresh = makeScene({ content_version: 4, elements: [el('el_00000001', 'theirs')] });
    mockGetScene.mockResolvedValue(fresh);

    const { result } = renderHook(() => useSceneSync(makeScene({ content_version: 1 })));
    act(() =>
      result.current.dispatchOps(
        [{ op: 'update', element_id: 'el_00000001', payload: { text: 'mine' } }],
        [el('el_00000001', 'mine')],
      ),
    );
    act(() => result.current.applyRemoteOps(remoteRow({ op_seq: 3 })));
    await drain();
    expect(result.current.saveState).toBe('conflict');

    act(() => result.current.resolveConflict('theirs'));
    expect(result.current.elements).toEqual([el('el_00000001', 'theirs')]);
    expect(result.current.version).toBe(4);
    expect(result.current.saveState).toBe('saved');
    expect(result.current.conflict).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// reconcile() — the windowed-out remount refetch (M1)
// ---------------------------------------------------------------------------
describe('reconcile — refetch this scene as truth', () => {
  it('adopts the server snapshot when local state is clean', async () => {
    const fresh = makeScene({ content_version: 7, elements: [el('el_00000042', 'server')] });
    mockGetScene.mockResolvedValue(fresh);

    const { result } = renderHook(() => useSceneSync(makeScene({ content_version: 1 })));
    act(() => result.current.reconcile());
    await drain();

    expect(mockGetScene).toHaveBeenCalledTimes(1);
    expect(result.current.version).toBe(7);
    expect(result.current.elements).toEqual([el('el_00000042', 'server')]);
    expect(result.current.saveState).toBe('saved');
    expect(result.current.conflict).toBeNull();
  });

  it('diverges into conflict when local edits are pending', async () => {
    mockApplyOps.mockReturnValue(deferred<never>().promise);
    mockGetScene.mockResolvedValue(
      makeScene({ content_version: 5, elements: [el('el_00000001', 'theirs')] }),
    );

    const { result } = renderHook(() => useSceneSync(makeScene({ content_version: 1 })));
    act(() =>
      result.current.dispatchOps(
        [{ op: 'update', element_id: 'el_00000001', payload: { text: 'mine' } }],
        [el('el_00000001', 'mine')],
      ),
    );
    act(() => result.current.reconcile());
    await drain();

    expect(mockGetScene).toHaveBeenCalledTimes(1);
    expect(result.current.saveState).toBe('conflict');
    expect(result.current.conflict).toEqual({
      mine: [el('el_00000001', 'mine')],
      theirs: [el('el_00000001', 'theirs')],
    });
  });
});
