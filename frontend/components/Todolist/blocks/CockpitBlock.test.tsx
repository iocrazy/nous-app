/**
 * Phase 2a §2 — the cockpit's target-level pause / resume. Typed outcome:
 * success re-reads the issue (onIssueChanged), failure lands on the cockpit.
 */
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CockpitBlockView } from './CockpitBlock';
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

  it('shows the failure on the cockpit instead of swallowing it', async () => {
    resumeIssue.mockRejectedValueOnce(new Error('run_state_unavailable'));
    render(<CockpitBlockView ctx={ctx('paused', vi.fn(), false)} />);
    fireEvent.click(screen.getByTestId('cockpit-resume'));
    const err = await screen.findByTestId('cockpit-control-error');
    expect(err.textContent).toBe('run_state_unavailable');
  });

  it('draws no controls when the issue is idle', () => {
    render(<CockpitBlockView ctx={ctx('idle', vi.fn(), false)} />);
    expect(screen.queryByTestId('cockpit-pause')).toBeNull();
    expect(screen.queryByTestId('cockpit-resume')).toBeNull();
  });
});
