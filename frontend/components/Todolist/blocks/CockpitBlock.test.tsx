/**
 * Phase 2a §2 — the cockpit's target-level pause / resume. Typed outcome:
 * success re-reads the issue (onIssueChanged), failure lands on the cockpit.
 */
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CockpitBlockView } from './CockpitBlock';
import { IssueControlError } from '../../../services/issuesService';
import type { IssueBlockContext } from '../issueBlocks';
import type { IssueProgress } from '../../../services/issuesService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));
const pauseIssue = vi.fn();
const resumeIssue = vi.fn();
vi.mock('../../../services/issuesService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../../services/issuesService')>();
  return { ...mod, pauseIssue: (...a: unknown[]) => pauseIssue(...a), resumeIssue: (...a: unknown[]) => resumeIssue(...a) };
});
vi.mock('../../../services/aiLibraryService', () => ({ aiLibraryService: { cancelRun: vi.fn(async () => ({})) } }));

afterEach(cleanup);
beforeEach(() => {
  pauseIssue.mockReset().mockResolvedValue({ issue_id: '5', paused_at: '2026-09-08T00:00:00Z', run_id: 'r1' });
  resumeIssue.mockReset().mockResolvedValue({ issue_id: '5', dispatched: true, reason: 'dispatched', workflow_id: 'wf', run_id: null });
});

function rollup(phase: IssueProgress['phase'], withRun = true): IssueProgress {
  return {
    issue_id: '5',
    status: 'in_progress',
    phase,
    paused_at: phase === 'paused' ? '2026-09-08T00:00:00Z' : null,
    current_run: withRun ? { id: 'r1', status: 'running', started_at: null, model: null, view: {}, cost: {} } : null,
    runs: [],
    sub_issues: { total: 0, done: 0, items: [] },
    inbox_pending: 0,
    budget: { budget_cents: null, spent_cents: 0, pct: null, state: 'ok' },
    origin: { kind: 'manual' },
    execution_state: {},
    computed_at: '2026-09-08T00:00:00Z',
  };
}

function ctx(phase: IssueProgress['phase'], onIssueChanged = vi.fn(), withRun = true): IssueBlockContext {
  return { issue: { id: '5', status: 'in_progress' }, rollup: rollup(phase, withRun), originKind: null, phase, env: { onIssueChanged } };
}

describe('CockpitBlockView — pause / resume (phase 2a §2)', () => {
  it('offers Pause while running and calls pauseIssue with the issue id', async () => {
    const onIssueChanged = vi.fn();
    render(<CockpitBlockView ctx={ctx('running', onIssueChanged)} />);
    expect(screen.queryByTestId('cockpit-resume')).toBeNull();
    fireEvent.click(screen.getByTestId('cockpit-pause'));
    await waitFor(() => expect(pauseIssue).toHaveBeenCalledWith(5));
    await waitFor(() => expect(onIssueChanged).toHaveBeenCalled());
    expect(screen.queryByTestId('cockpit-control-error')).toBeNull();
  });

  it('offers Resume while paused (no Pause) and calls resumeIssue', async () => {
    render(<CockpitBlockView ctx={ctx('paused', vi.fn(), false)} />);
    expect(screen.queryByTestId('cockpit-pause')).toBeNull();
    fireEvent.click(screen.getByTestId('cockpit-resume'));
    await waitFor(() => expect(resumeIssue).toHaveBeenCalledWith(5));
  });

  it('shows the failure on the cockpit as copy for the code, never the raw body', async () => {
    // The service's documented rejection: code from detail.code, message = server text.
    resumeIssue.mockRejectedValueOnce(new IssueControlError('run_state_unavailable', 503, 'run state unavailable'));
    render(<CockpitBlockView ctx={ctx('paused', vi.fn(), false)} />);
    fireEvent.click(screen.getByTestId('cockpit-resume'));
    const err = await screen.findByTestId('cockpit-control-error');
    expect(err.textContent).toBe('Run state unavailable — try again in a moment');
  });

  it('says generic for a network failure instead of leaking the stack', async () => {
    pauseIssue.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    render(<CockpitBlockView ctx={ctx('running')} />);
    fireEvent.click(screen.getByTestId('cockpit-pause'));
    const err = await screen.findByTestId('cockpit-control-error');
    expect(err.textContent).toBe('Could not update the issue');
  });

  it('draws no controls when the issue is idle', () => {
    render(<CockpitBlockView ctx={ctx('idle', vi.fn(), false)} />);
    expect(screen.queryByTestId('cockpit-pause')).toBeNull();
    expect(screen.queryByTestId('cockpit-resume')).toBeNull();
  });
});

// ── harness 2b-1 §1: replay "as of step N" ──────────────────────────────────
import { ReplayContext } from '../replayContext';

describe('CockpitBlockView — replay as-of (harness 2b-1 §1)', () => {
  const frozenView = { v: 1, phase: 'running', step: { done: 7, total: 12, label: 'Scene 7' }, current: { turn: 1, step: 7, model: 'm' }, retry: null, context: null, blocked: null, children: { total: 0, done: 0 }, ended: null, inbox_pending: 0, budget: null, question: null, last_answer: null, revision: 30 } as never;

  it('reads the frozen view, shows the badge and disables the controls', () => {
    render(
      <ReplayContext.Provider value={{ runId: 'r1', seq: 30, view: frozenView, cost: null, loading: false, seek: vi.fn() }}>
        <CockpitBlockView ctx={ctx('running')} />
      </ReplayContext.Provider>,
    );
    expect(screen.getByTestId('cockpit-steps').textContent).toContain('7');
    expect(screen.getByTestId('cockpit-steps').textContent).toContain('12');
    // the file's t() mock echoes the fallback template — interpolation is i18next's job
    expect(screen.getByTestId('cockpit-asof').textContent).toContain('as of step');
    expect((screen.getByTestId('cockpit-pause') as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId('cockpit-cancel') as HTMLButtonElement).disabled).toBe(true);
  });

  it('ignores a replay of some other run and stays live', () => {
    render(
      <ReplayContext.Provider value={{ runId: 'r-other', seq: 30, view: frozenView, cost: null, loading: false, seek: vi.fn() }}>
        <CockpitBlockView ctx={ctx('running')} />
      </ReplayContext.Provider>,
    );
    expect(screen.queryByTestId('cockpit-asof')).toBeNull();
    expect((screen.getByTestId('cockpit-pause') as HTMLButtonElement).disabled).toBe(false);
  });

  it('seq null (Live) is not frozen even when attached', () => {
    render(
      <ReplayContext.Provider value={{ runId: 'r1', seq: null, view: null, cost: null, loading: false, seek: vi.fn() }}>
        <CockpitBlockView ctx={ctx('running')} />
      </ReplayContext.Provider>,
    );
    expect(screen.queryByTestId('cockpit-asof')).toBeNull();
  });
});
