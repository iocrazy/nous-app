import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getRun = vi.fn();
const undoRun = vi.fn();
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    getRun: (...args: unknown[]) => getRun(...args),
    undoRun: (...args: unknown[]) => undoRun(...args),
  },
}));

import { onStoryboardRefresh } from './shotFocusBus';
import { __clearRunUndoCache, useRunUndo } from './useRunUndo';
import type { AgentRunDetail, AgentRunUndoReport } from '../../types';

function makeRun(overrides: Partial<AgentRunDetail> = {}): AgentRunDetail {
  return {
    id: 'run-1',
    agent_id: 'agent-1',
    status: 'completed',
    trigger: 'chat',
    prompt_tokens: 10,
    completion_tokens: 20,
    total_tokens: 30,
    started_at: '2026-08-09T00:00:00Z',
    skill_slugs_used: [],
    heartbeat_at: '2026-08-09T00:00:00Z',
    cancel_requested: false,
    metadata_json: {},
    created_at: '2026-08-09T00:00:00Z',
    undone_at: null,
    ...overrides,
  } as AgentRunDetail;
}

function makeReport(overrides: Partial<AgentRunUndoReport> = {}): AgentRunUndoReport {
  return {
    status: 'done',
    shots_deleted: 1,
    shots_reverted: 0,
    scene_elements_reverted: 0,
    skipped: [],
    ...overrides,
  };
}

beforeEach(() => {
  __clearRunUndoCache();
  getRun.mockReset();
  undoRun.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useRunUndo', () => {
  it('is hidden with no fetch when disabled', () => {
    const { result } = renderHook(() => useRunUndo('run-1', false));
    expect(result.current.state).toBe('hidden');
    expect(getRun).not.toHaveBeenCalled();
  });

  it('is hidden with no fetch when runId is missing', () => {
    const { result } = renderHook(() => useRunUndo(null, true));
    expect(result.current.state).toBe('hidden');
    expect(getRun).not.toHaveBeenCalled();
  });

  it('resolves to ready when getRun returns a run without undone_at', async () => {
    getRun.mockResolvedValue(makeRun({ undone_at: null }));
    const { result } = renderHook(() => useRunUndo('run-1', true));
    expect(result.current.state).toBe('loading');
    await waitFor(() => expect(result.current.state).toBe('ready'));
    expect(getRun).toHaveBeenCalledWith('run-1');
  });

  it('resolves to undone when getRun returns a run with undone_at set', async () => {
    getRun.mockResolvedValue(makeRun({ undone_at: '2026-08-09T01:00:00Z' }));
    const { result } = renderHook(() => useRunUndo('run-1', true));
    await waitFor(() => expect(result.current.state).toBe('undone'));
  });

  it('hides and logs on getRun failure (e.g. 404 for another user\'s run)', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    getRun.mockRejectedValue(new Error('not found'));
    const { result } = renderHook(() => useRunUndo('run-1', true));
    await waitFor(() => expect(result.current.state).toBe('hidden'));
    expect(consoleError).toHaveBeenCalled();
  });

  it('serves state from the module cache without refetching on a second mount', async () => {
    getRun.mockResolvedValue(makeRun({ undone_at: null }));
    const first = renderHook(() => useRunUndo('run-1', true));
    await waitFor(() => expect(first.result.current.state).toBe('ready'));
    expect(getRun).toHaveBeenCalledTimes(1);

    const second = renderHook(() => useRunUndo('run-1', true));
    // Cached synchronously — no intermediate 'loading' state, no second fetch.
    expect(second.result.current.state).toBe('ready');
    expect(getRun).toHaveBeenCalledTimes(1);
  });

  it('does not update state after unmount while the fetch is still in flight', async () => {
    let resolveFetch!: (run: AgentRunDetail) => void;
    getRun.mockReturnValue(
      new Promise<AgentRunDetail>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    const { result, unmount } = renderHook(() => useRunUndo('run-1', true));
    expect(result.current.state).toBe('loading');
    unmount();

    await act(async () => {
      resolveFetch(makeRun({ undone_at: null }));
      // Let the promise microtask queue flush.
      await Promise.resolve();
    });

    // No React "state update on unmounted component" warning, and the last
    // read state is still the pre-unmount snapshot — never flips to 'ready'.
    expect(result.current.state).toBe('loading');
  });

  it('undo() success stores the report, moves to undone, caches it, and fires a storyboard refresh', async () => {
    getRun.mockResolvedValue(makeRun({ undone_at: null }));
    const report = makeReport({ shots_deleted: 3 });
    undoRun.mockResolvedValue(report);
    const refreshListener = vi.fn();
    const unsubscribe = onStoryboardRefresh(refreshListener);

    const { result } = renderHook(() => useRunUndo('run-1', true));
    await waitFor(() => expect(result.current.state).toBe('ready'));

    await act(async () => {
      await result.current.undo();
    });

    expect(undoRun).toHaveBeenCalledWith('run-1');
    expect(result.current.state).toBe('undone');
    expect(result.current.report).toEqual(report);
    expect(refreshListener).toHaveBeenCalledTimes(1);

    // Cache written: a fresh mount for the same runId resolves synchronously.
    const second = renderHook(() => useRunUndo('run-1', true));
    expect(second.result.current.state).toBe('undone');
    expect(getRun).toHaveBeenCalledTimes(1); // no extra fetch triggered by the second mount

    unsubscribe();
  });

  it('undo() failure logs the error, falls back to ready, and does not refresh the storyboard', async () => {
    getRun.mockResolvedValue(makeRun({ undone_at: null }));
    undoRun.mockRejectedValue(new Error('undo boom'));
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const refreshListener = vi.fn();
    const unsubscribe = onStoryboardRefresh(refreshListener);

    const { result } = renderHook(() => useRunUndo('run-1', true));
    await waitFor(() => expect(result.current.state).toBe('ready'));

    await act(async () => {
      await result.current.undo();
    });

    expect(consoleError).toHaveBeenCalled();
    expect(result.current.state).toBe('ready');
    expect(result.current.report).toBeNull();
    expect(refreshListener).not.toHaveBeenCalled();

    unsubscribe();
  });

  it('undo() is a no-op when runId is missing', async () => {
    const { result } = renderHook(() => useRunUndo(null, true));
    expect(result.current.state).toBe('hidden');
    await act(async () => {
      await result.current.undo();
    });
    expect(undoRun).not.toHaveBeenCalled();
    expect(result.current.state).toBe('hidden');
  });
});
