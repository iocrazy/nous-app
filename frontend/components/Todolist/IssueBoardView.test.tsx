/**
 * Phase 2a §4 — board cards carry the same phase / queued / action language
 * as list rows, and a blocked card says why (clipped; full text in the title).
 */
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { IssueBoardView, blockedReason, clipReason, BOARD_REASON_MAX } from './IssueBoardView';
import type { UiIssue } from './types';
import type { Issue } from '../../services/issuesService';

vi.mock('react-i18next', () => ({
  // i18next-shaped: a string second arg is the fallback, an object carries
  // count + defaultValue (queuedChip's call).
  useTranslation: () => ({
    t: (key: string, arg?: string | { count?: number; defaultValue?: string }) =>
      typeof arg === 'object' && arg ? (arg.defaultValue ?? key).replace('{{count}}', String(arg.count)) : arg ?? key,
  }),
}));

afterEach(cleanup);

function mk(over: Partial<UiIssue> & Pick<UiIssue, 'id' | 'identifier' | 'title' | 'status'>, raw: Record<string, unknown> = {}): UiIssue {
  return {
    description: null,
    priority: 'medium',
    parent_id: null,
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
    last_activity_at: '2026-09-08T00:00:00Z',
    ...over,
    raw: { id: over.id, status: over.status, dbos_workflow_id: null, execution_state: null, ...raw } as unknown as Issue,
  } as UiIssue;
}

function renderBoard(issues: UiIssue[], pendingSummary?: Record<string, { count: number }>) {
  return render(
    <MemoryRouter initialEntries={['/team/8/todolist']}>
      <Routes>
        <Route path="/team/:teamId/todolist" element={<IssueBoardView issues={issues} pendingSummary={pendingSummary} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('IssueBoardView — phase 2a §4 card footer', () => {
  it('shows the phase chip, the queued count and Resume on a paused card', () => {
    renderBoard(
      [mk({ id: 5, identifier: 'MH-5', title: 'Paused with mail', status: 'in_progress' }, { dbos_workflow_id: 'wf-5', paused_at: '2026-09-08T00:00:00Z' })],
      { '5': { count: 2 } },
    );
    expect(screen.getByTestId('board-phase-chip').getAttribute('data-phase')).toBe('paused');
    expect(screen.getByTestId('board-queued-chip').textContent).toBe('2 queued');
    expect(screen.getByTestId('board-action').textContent).toBe('Resume');
  });

  it('offers Steer on a running card and draws no queued chip at zero', () => {
    renderBoard(
      [mk({ id: 6, identifier: 'MH-6', title: 'Running', status: 'in_progress' }, { dbos_workflow_id: 'wf-6' })],
      { '6': { count: 0 } },
    );
    expect(screen.getByTestId('board-phase-chip').getAttribute('data-phase')).toBe('running');
    expect(screen.queryByTestId('board-queued-chip')).toBeNull();
    expect(screen.getByTestId('board-action').textContent).toBe('Steer');
  });

  it('draws neither chip nor action on an idle card', () => {
    renderBoard([mk({ id: 7, identifier: 'MH-7', title: 'Idle', status: 'backlog' })]);
    expect(screen.queryByTestId('board-phase-chip')).toBeNull();
    expect(screen.queryByTestId('board-action')).toBeNull();
  });

  it('clips a long blocked reason on the chip but keeps the full text in the title', () => {
    const full = 'x'.repeat(BOARD_REASON_MAX + 40);
    renderBoard([mk({ id: 8, identifier: 'MH-8', title: 'Blocked', status: 'blocked' }, { execution_state: { error_message: full } })]);
    const chip = screen.getByTestId('board-blocked-reason');
    expect(chip.textContent).toBe(`${'x'.repeat(BOARD_REASON_MAX)}…`);
    expect(chip.getAttribute('title')).toBe(full);
  });

  it('gates the reason on the derived phase: a paused issue whose status is still blocked reads as paused', () => {
    renderBoard([mk({ id: 10, identifier: 'MH-10', title: 'Paused but blocked', status: 'blocked' }, { paused_at: '2026-09-08T00:00:00Z', execution_state: { error_message: 'stale' } })]);
    expect(screen.queryByTestId('board-blocked-reason')).toBeNull();
    expect(screen.getByTestId('board-phase-chip').getAttribute('data-phase')).toBe('paused');
  });

  it('falls back to outcome_reason and stays silent when there is no reason', () => {
    expect(blockedReason(mk({ id: 9, identifier: 'MH-9', title: 'b', status: 'blocked' }, { execution_state: { outcome_reason: 'budget_exhausted' } }))).toBe('budget_exhausted');
    expect(blockedReason(mk({ id: 9, identifier: 'MH-9', title: 'b', status: 'blocked' }))).toBeNull();
    expect(blockedReason(mk({ id: 9, identifier: 'MH-9', title: 'b', status: 'in_progress' }, { execution_state: { error_message: 'x' } }))).toBeNull();
    expect(clipReason('short')).toBe('short');
  });
});
