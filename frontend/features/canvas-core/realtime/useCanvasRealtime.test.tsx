/**
 * Tests for useCanvasRealtime (Phase 6a).
 *
 * Verifies:
 *   1. Subscribes on mount with the correct postgres_changes filter
 *   2. Calls applyRemoteUpdate when an UPDATE event fires
 *   3. Removes the channel on unmount (no leak)
 *   4. No-ops when canvasId is null / undefined
 */

import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { Canvas } from '../types';
import { useCanvasRealtime } from './useCanvasRealtime';

// ─── Mock Supabase client ─────────────────────────────────────────────────────
// Capture the postgres_changes UPDATE callback so we can trigger it in tests.
let capturedUpdateCallback:
  | ((payload: { new: unknown }) => void)
  | null = null;

const mockChannel = {
  on: vi.fn(
    (
      _event: string,
      _opts: Record<string, unknown>,
      callback?: (payload: { new: unknown }) => void,
    ) => {
      if (callback) capturedUpdateCallback = callback;
      return mockChannel;
    },
  ),
  subscribe: vi.fn(() => mockChannel),
};

const mockRemoveChannel = vi.fn();
const mockSupabase = {
  channel: vi.fn(() => mockChannel),
  removeChannel: mockRemoveChannel,
};

vi.mock('../../../supabaseClient', () => ({
  getSupabaseClient: () => mockSupabase,
}));

// ─── Constants ────────────────────────────────────────────────────────────────
const TS0 = '2026-06-14T10:00:00.000Z';
const TS1 = '2026-06-14T10:00:01.000Z';

const baseCanvas: Canvas = {
  id: '42',
  project_id: '7',
  name: 'Hook Test Canvas',
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

// ─── Setup / Teardown ─────────────────────────────────────────────────────────
// Save the original applyRemoteUpdate so any test that patches it can be
// cleaned up without leaking into subsequent tests.
let originalApplyRemoteUpdate: ReturnType<
  typeof useCanvasCoreStore.getState
>['applyRemoteUpdate'];

beforeEach(() => {
  capturedUpdateCallback = null;
  vi.clearAllMocks();

  // Snapshot the real action before tests run.
  originalApplyRemoteUpdate = useCanvasCoreStore.getState().applyRemoteUpdate;

  // Seed a clean loaded-canvas state.
  useCanvasCoreStore.setState({
    canvasId: '42',
    baseUpdatedAt: TS0,
    loadStatus: 'ready',
    nodes: [],
    connections: [],
    revision: 0,
    persistedRevision: 0,
    conflict: null,
    applyRemoteUpdate: originalApplyRemoteUpdate,
  });
});

afterEach(() => {
  // Restore the real action if a test replaced it with a mock.
  useCanvasCoreStore.setState({
    applyRemoteUpdate: originalApplyRemoteUpdate,
  });
  useCanvasCoreStore.getState().reset();
});

// ─────────────────────────────────────────────────────────────────────────────
describe('useCanvasRealtime — subscription setup', () => {
  it('opens a channel filtered to the canvas id on mount', () => {
    renderHook(() => useCanvasRealtime('42'));

    expect(mockSupabase.channel).toHaveBeenCalledWith(
      expect.stringContaining('42'),
    );
    expect(mockChannel.on).toHaveBeenCalledWith(
      'postgres_changes',
      expect.objectContaining({
        event: 'UPDATE',
        schema: 'public',
        table: 'canvases',
        filter: 'id=eq.42',
      }),
      expect.any(Function),
    );
    expect(mockChannel.subscribe).toHaveBeenCalledTimes(1);
  });

  it('removes the channel on unmount (no leak)', () => {
    const { unmount } = renderHook(() => useCanvasRealtime('42'));
    unmount();
    expect(mockRemoveChannel).toHaveBeenCalledWith(mockChannel);
  });
});

describe('useCanvasRealtime — no-op on missing canvasId', () => {
  it('does not open a channel when canvasId is null', () => {
    renderHook(() => useCanvasRealtime(null));
    expect(mockSupabase.channel).not.toHaveBeenCalled();
  });

  it('does not open a channel when canvasId is undefined', () => {
    renderHook(() => useCanvasRealtime(undefined));
    expect(mockSupabase.channel).not.toHaveBeenCalled();
  });
});

describe('useCanvasRealtime — UPDATE event handling', () => {
  it('calls applyRemoteUpdate when an UPDATE payload arrives (mock-based)', () => {
    const mockApply = vi.fn();
    // Temporarily replace the action to spy on calls.
    useCanvasCoreStore.setState({ applyRemoteUpdate: mockApply });

    renderHook(() => useCanvasRealtime('42'));

    expect(capturedUpdateCallback).not.toBeNull();

    const remoteRow: Canvas = {
      ...baseCanvas,
      base_updated_at: TS1,
      nodes_json: [{ id: 'server-node' }],
    };
    capturedUpdateCallback!({ new: remoteRow });

    expect(mockApply).toHaveBeenCalledWith(remoteRow);
  });

  it('applyRemoteUpdate is wired to the real store action end-to-end', () => {
    // Integration: real store, no mocks — verify state change happens.
    renderHook(() => useCanvasRealtime('42'));

    expect(capturedUpdateCallback).not.toBeNull();

    const remoteRow: Canvas = {
      ...baseCanvas,
      base_updated_at: TS1,
      nodes_json: [{ id: 'from-realtime' }],
    };
    capturedUpdateCallback!({ new: remoteRow });

    const s = useCanvasCoreStore.getState();
    // Clean store (revision === persistedRevision === 0) → should rebase.
    expect(s.baseUpdatedAt).toBe(TS1);
    expect(s.nodes).toEqual([{ id: 'from-realtime' }]);
    expect(s.conflict).toBeNull();
  });
});
