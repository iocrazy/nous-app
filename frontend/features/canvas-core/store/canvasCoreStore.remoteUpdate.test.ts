/**
 * TDD tests for canvasCoreStore.applyRemoteUpdate (Phase 6a — Realtime sync).
 *
 * applyRemoteUpdate is called by useCanvasRealtime whenever Supabase
 * Realtime broadcasts an UPDATE on the canvases table. Three behaviours:
 *
 *   A) Self-echo guard — row.base_updated_at <= current baseUpdatedAt → no-op
 *   B) Newer + no dirty edits → rebase to remote row (happy-path sync)
 *   C) Newer + unsaved local edits → set conflict (reuse 409 path), no clobber
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Canvas } from '../types';
import { createCanvasCoreStore } from './canvasCoreStore';

const TS0 = '2026-06-14T10:00:00.000Z';
const TS1 = '2026-06-14T10:00:01.000Z'; // 1 s newer
const TS2 = '2026-06-14T10:00:02.000Z'; // 2 s newer

const baseCanvas: Canvas = {
  id: '100',
  project_id: '10',
  name: 'Test Canvas',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [],
  connections_json: [],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: TS0,
  created_at: TS0,
  updated_at: TS0,
  created_by: null,
};

function makeStore(initial: Canvas = baseCanvas) {
  const loadImpl = vi.fn(async () => ({ ...initial }));
  const saveImpl = vi.fn(async () => ({
    ok: true as const,
    canvas: { ...initial, base_updated_at: TS1 },
  }));
  return {
    store: createCanvasCoreStore({ loadImpl, saveImpl, debounceMs: 9999 }),
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

// ─────────────────────────────────────────────────────────────────────────────
// A. Self-echo guard
// ─────────────────────────────────────────────────────────────────────────────

describe('applyRemoteUpdate — self-echo guard', () => {
  it('no-ops when remote base_updated_at equals current', async () => {
    const { store } = makeStore();
    await store.getState().loadCanvas('100');

    const before = store.getState().nodes;
    const remoteRow: Canvas = {
      ...baseCanvas,
      base_updated_at: TS0, // same
      nodes_json: [{ id: 'should-be-ignored' }],
    };
    store.getState().applyRemoteUpdate(remoteRow);

    expect(store.getState().nodes).toBe(before);
    expect(store.getState().baseUpdatedAt).toBe(TS0);
    expect(store.getState().conflict).toBeNull();
  });

  it('no-ops when remote base_updated_at is older than current', async () => {
    const { store } = makeStore({ ...baseCanvas, base_updated_at: TS2 });
    await store.getState().loadCanvas('100');

    const remoteRow: Canvas = {
      ...baseCanvas,
      base_updated_at: TS1, // older than TS2 in store
      nodes_json: [{ id: 'stale-node' }],
    };
    store.getState().applyRemoteUpdate(remoteRow);

    expect(store.getState().nodes).toEqual([]);
    expect(store.getState().conflict).toBeNull();
  });

  it('no-ops when store has not loaded yet (baseUpdatedAt is null)', () => {
    const { store } = makeStore();
    // Do NOT call loadCanvas — baseUpdatedAt is null.
    const remoteRow: Canvas = { ...baseCanvas, base_updated_at: TS1 };
    store.getState().applyRemoteUpdate(remoteRow);
    expect(store.getState().loadStatus).toBe('idle');
    expect(store.getState().conflict).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// B. Rebase (newer row, no unsaved local edits)
// ─────────────────────────────────────────────────────────────────────────────

describe('applyRemoteUpdate — rebase', () => {
  it('applies the remote row when there are no local unsaved edits', async () => {
    const { store } = makeStore();
    await store.getState().loadCanvas('100');
    // revision === persistedRevision === 0 → clean

    const remoteNodes = [{ id: 'from-server' }];
    const remoteRow: Canvas = {
      ...baseCanvas,
      base_updated_at: TS1,
      nodes_json: remoteNodes,
      connections_json: [{ id: 'c1' }],
    };
    store.getState().applyRemoteUpdate(remoteRow);

    const s = store.getState();
    expect(s.baseUpdatedAt).toBe(TS1);
    expect(s.nodes).toEqual(remoteNodes);
    expect(s.connections).toEqual([{ id: 'c1' }]);
    expect(s.conflict).toBeNull();
    // applyServerRow resets revision counters
    expect(s.revision).toBe(0);
    expect(s.persistedRevision).toBe(0);
  });

  it('clears a prior conflict when rebasing cleanly', async () => {
    const { store } = makeStore();
    await store.getState().loadCanvas('100');

    // Inject a prior conflict state
    const priorConflict: Canvas = { ...baseCanvas, base_updated_at: TS1 };
    store.setState({ conflict: priorConflict });

    // A newer remote row with no dirty edits should rebase and clear conflict.
    const newerRow: Canvas = { ...baseCanvas, base_updated_at: TS2 };
    store.getState().applyRemoteUpdate(newerRow);

    expect(store.getState().baseUpdatedAt).toBe(TS2);
    expect(store.getState().conflict).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// C. Conflict (newer row, dirty local edits → never clobber)
// ─────────────────────────────────────────────────────────────────────────────

describe('applyRemoteUpdate — conflict on dirty local edits', () => {
  it('sets conflict and preserves local edits', async () => {
    const { store } = makeStore();
    await store.getState().loadCanvas('100');

    // Introduce an unsaved local edit (revision > persistedRevision)
    const localNodes = [{ id: 'local-unsaved' }];
    store.getState().setNodes(localNodes);
    expect(store.getState().revision).toBe(1);
    expect(store.getState().persistedRevision).toBe(0);

    const remoteRow: Canvas = {
      ...baseCanvas,
      base_updated_at: TS1,
      nodes_json: [{ id: 'remote-would-clobber' }],
    };
    store.getState().applyRemoteUpdate(remoteRow);

    const s = store.getState();
    // Local nodes must be untouched
    expect(s.nodes).toEqual(localNodes);
    // Conflict is surfaced (the exact remote row)
    expect(s.conflict).toEqual(remoteRow);
    // baseUpdatedAt must NOT change
    expect(s.baseUpdatedAt).toBe(TS0);
  });

  it('does not clobber local edits even when the remote is much newer', async () => {
    const { store } = makeStore();
    await store.getState().loadCanvas('100');
    store.getState().setNodes([{ id: 'precious-local' }]);

    const remoteRow: Canvas = {
      ...baseCanvas,
      base_updated_at: TS2, // significantly newer
    };
    store.getState().applyRemoteUpdate(remoteRow);

    expect(store.getState().nodes).toEqual([{ id: 'precious-local' }]);
    expect(store.getState().conflict).not.toBeNull();
    expect(store.getState().baseUpdatedAt).toBe(TS0);
  });
});
