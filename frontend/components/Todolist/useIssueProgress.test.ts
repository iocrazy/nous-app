/**
 * The polling half of the turn signal (harness 3b §4).
 *
 * `IssueProgress` has no "a turn ended" field — the fact only exists as an
 * EDGE between two reads: the run that was current is no longer current. The
 * derivation therefore lives inside this hook, holding the previous progress,
 * rather than in each consumer: polling fires several times per turn, and every
 * consumer re-deriving it would each need its own copy of the last answer.
 *
 * The bodies are the real `GET /issues/{id}/progress` shape — ids as STRINGS
 * (Snowflake BIGINTs), `last_seq` a number or null (an old backend omits it).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const { getIssueProgress, notifyTurn } = vi.hoisted(() => ({
  getIssueProgress: vi.fn(),
  notifyTurn: vi.fn(),
}));

vi.mock('../../services/issuesService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/issuesService')>()),
  getIssueProgress,
}));
vi.mock('./issueTurnSignal', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./issueTurnSignal')>()),
  notifyTurn,
}));
vi.mock('../../supabaseClient', () => ({ getSupabaseClient: () => null }));

import { useIssueProgress } from './useIssueProgress';

const RUN = '727145299382534100';

const base = {
  issue_id: '5',
  status: 'in_progress',
  phase: 'running',
  paused_at: null,
  runs: [],
  sub_issues: { total: 0, done: 0, items: [] },
  inbox_pending: 0,
  budget: { budget_cents: null, spent_cents: 0, pct: null, state: 'ok' },
  // 3c §3.3：后端对每个议题都发这个键（没跑过 run 也是零值而不是缺席）。
  efficiency: { runs: 0, steps: 0, tool_calls: 0, tool_errors: 0, deliverables: 0, avg_run_ms: null, cost_per_deliverable_cents: null, turn_end_reasons: {} },
  origin: { kind: 'manual', origin_id: null },
  execution_state: {},
  computed_at: '2026-09-14T00:00:00Z',
};

const run = (id: string, lastSeq: number | null) => ({
  id,
  status: 'running',
  started_at: null,
  model: null,
  last_seq: lastSeq,
  view: {},
  cost: {},
});

beforeEach(() => {
  getIssueProgress.mockReset();
  notifyTurn.mockReset();
});

describe('useIssueProgress — the turn signal', () => {
  it('signals the turn that just ended, once, with the run it ended', async () => {
    getIssueProgress.mockResolvedValueOnce({ ...base, phase: 'running', current_run: run(RUN, 42) });
    getIssueProgress.mockResolvedValueOnce({ ...base, phase: 'idle', current_run: null });

    const { result } = renderHook(() => useIssueProgress(5, 's1'));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(notifyTurn).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.refresh();
    });
    expect(notifyTurn).toHaveBeenCalledTimes(1);
    expect(notifyTurn).toHaveBeenCalledWith('5', { runId: RUN, seq: 42 });
  });

  it('the first load signals nothing', async () => {
    // Without the `endedRun &&` guard this fires on mount — there is no prior
    // run, so nothing ended, and every consumer would re-fetch on arrival.
    getIssueProgress.mockResolvedValue({ ...base, current_run: run(RUN, 7) });
    const { result } = renderHook(() => useIssueProgress(5, 's1'));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(notifyTurn).not.toHaveBeenCalled();
  });

  it('says nothing while the same run keeps running', async () => {
    getIssueProgress.mockResolvedValue({ ...base, current_run: run(RUN, 12) });
    const { result } = renderHook(() => useIssueProgress(5, 's1'));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(async () => {
      await result.current.refresh();
    });
    expect(notifyTurn).not.toHaveBeenCalled();
  });

  it('signals the old run when one run is replaced by the next', async () => {
    getIssueProgress.mockResolvedValueOnce({ ...base, current_run: run(RUN, 42) });
    getIssueProgress.mockResolvedValueOnce({ ...base, current_run: run('727145299382534200', 1) });
    const { result } = renderHook(() => useIssueProgress(5, 's1'));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(async () => {
      await result.current.refresh();
    });
    expect(notifyTurn).toHaveBeenCalledWith('5', { runId: RUN, seq: 42 });
  });

  it('an old backend with no last_seq still signals, at seq 0', async () => {
    // `last_seq` arrives with Task 4b. Missing is 0 — a lane that has never
    // seen a signal fires on it, which is the behaviour that matters.
    const { last_seq: _omitted, ...noSeq } = run(RUN, 42);
    getIssueProgress.mockResolvedValueOnce({ ...base, current_run: noSeq });
    getIssueProgress.mockResolvedValueOnce({ ...base, current_run: null });
    const { result } = renderHook(() => useIssueProgress(5, 's1'));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(async () => {
      await result.current.refresh();
    });
    expect(notifyTurn).toHaveBeenCalledWith('5', { runId: RUN, seq: 0 });
  });
});
