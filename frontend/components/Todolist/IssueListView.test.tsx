import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
// The strip's three sources: the TaskManager feed (app-wide provider, absent
// here), the approvals endpoint, and scopedIssues (already in props).
vi.mock('../../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ needsInputItems: [] }),
}));
// Phase 2a §4: the queued-comment summary is its own endpoint; the mock is the
// wire shape (string ids), not the UiIssue shape.
const fetchPendingSummary = vi.fn(async (): Promise<Record<string, { count: number; oldestAt: string }>> => ({}));
vi.mock('../../services/agentInboxService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../services/agentInboxService')>();
  return { ...mod, fetchPendingSummary: () => fetchPendingSummary() };
});
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    listApprovalRequests: vi.fn(async () => ({ items: [], count: 0 })),
    approveRequest: vi.fn(async () => ({ id: 'x', status: 'approved' })),
    rejectRequest: vi.fn(async () => ({ id: 'x', status: 'rejected' })),
  },
}));

import { IssueListView } from './IssueListView';
import type { UiIssue } from './types';
import type { Issue } from '../../services/issuesService';

function mkIssue(over: Partial<UiIssue> & Pick<UiIssue, 'id' | 'identifier' | 'title' | 'status'>): UiIssue {
  const raw = {
    id: over.id,
    identifier: over.identifier,
    title: over.title,
    status: over.status,
    priority: over.priority ?? 'medium',
    project_id: null,
    assignee_user_id: null,
    assignee_agent_id: null,
    created_by_user_id: null,
    created_by_agent_id: null,
    dbos_workflow_id: over.raw?.dbos_workflow_id ?? null,
    origin_kind: 'manual',
    parent_id: null,
  } as unknown as Issue;
  return {
    id: over.id,
    identifier: over.identifier,
    title: over.title,
    description: null,
    status: over.status,
    priority: over.priority ?? 'medium',
    parent_id: null,
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
    last_activity_at: '2026-07-15T00:00:00Z',
    raw,
    ...over,
  } as UiIssue;
}

function renderList(issues: UiIssue[], viewMode: 'list' | 'board' = 'list', selectedIssueId: number | null = null) {
  return render(
    <MemoryRouter initialEntries={['/team/8/todolist']}>
      <Routes>
        <Route
          path="/team/:teamId/todolist"
          element={
            <IssueListView
              issues={issues}
              loading={false}
              error={null}
              viewMode={viewMode}
              onViewModeChange={vi.fn()}
              onNewIssue={vi.fn()}
              onRefresh={vi.fn()}
              agents={[]}
              scope={{ type: 'team', teamId: '8' }}
              selectedIssueId={selectedIssueId}
            />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('IssueListView', () => {
  it('renders the toolbar, rows, and pipeline without crashing', () => {
    renderList([
      mkIssue({ id: 1, identifier: 'MH-1', title: 'Ship the teaser', status: 'backlog' }),
      mkIssue({ id: 2, identifier: 'MH-2', title: 'Cut the trailer', status: 'in_progress' }),
    ]);
    expect(screen.getByText('New Issue')).toBeTruthy();
    expect(screen.getByText('Ship the teaser')).toBeTruthy();
    expect(screen.getByText('Cut the trailer')).toBeTruthy();
    // Pipeline capsule for backlog exists as a clickable button (title carries count).
    expect(screen.getByTitle('Backlog · 1')).toBeTruthy();
    expect(screen.getByTitle('In Progress · 1')).toBeTruthy();
  });

  it('shows a running pulse for a dispatched, non-terminal issue', () => {
    renderList([
      mkIssue({
        id: 3, identifier: 'MH-3', title: 'Rendering now', status: 'in_progress',
        raw: { dbos_workflow_id: 'wf-123' } as Issue,
      }),
    ]);
    expect(screen.getByText('running')).toBeTruthy();
  });

  it('does not show running for a done issue even if dispatched', () => {
    renderList([
      mkIssue({
        id: 4, identifier: 'MH-4', title: 'Already shipped', status: 'done',
        raw: { dbos_workflow_id: 'wf-999' } as Issue,
      }),
    ]);
    expect(screen.queryByText('running')).toBeNull();
  });

  it('renders the instructional empty state when there are no issues', () => {
    renderList([]);
    expect(screen.getByText(/create one/i)).toBeTruthy();
  });
});

describe('IssueListView — A1 注意力 chip', () => {
  it('suffixes the running chip with the turn number', () => {
    renderList([
      mkIssue({
        id: 9,
        identifier: 'MH-9',
        title: 'Long dispatch',
        status: 'in_progress',
        raw: { dbos_workflow_id: 'wf-9', execution_state: { turn: 3 } } as never,
      }),
    ]);
    expect(screen.getByText(/running · turn 3/)).toBeTruthy();
  });

  it('renders the needs-your-reply chip with the question as its tooltip', () => {
    const { container } = renderList([
      mkIssue({
        id: 10,
        identifier: 'MH-10',
        title: 'Waiting on me',
        status: 'needs_followup',
        raw: {
          execution_state: { agent_outcome: 'needs_input', outcome_reason: 'Monday or Wednesday?' },
        } as never,
      }),
    ]);
    const chip = container.querySelector('[data-testid="needs-reply-chip"]') as HTMLElement;
    expect(chip).not.toBeNull();
    expect(chip.getAttribute('title')).toBe('Monday or Wednesday?');
  });

  it('does not render the reply chip for an issue nobody asked about', () => {
    const { container } = renderList([
      mkIssue({ id: 11, identifier: 'MH-11', title: 'Plain', status: 'in_progress' }),
    ]);
    expect(container.querySelector('[data-testid="needs-reply-chip"]')).toBeNull();
  });
});

/**
 * A1 pins the strip "between the Quick row and the list" — i.e. above the view
 * container, not inside whichever view happens to be mounted. That is how it
 * is built (the strip is a sibling of the scroll container, and the
 * list/board switch happens inside it), and it is easy to undo by accident:
 * moving the mount one level down, into the list branch, still looks right in
 * every screenshot taken in list mode.
 *
 * Note the mount deliberately lives HERE and not in TodolistPage —
 * components/workspace/WorkspaceTasks.tsx renders IssueListView too, and the
 * project surface would silently lose the strip if it moved up a level.
 */
describe('IssueListView — 「等我的」横条跨视图', () => {
  const inReview = () =>
    mkIssue({ id: 20, identifier: 'MH-20', title: 'Second act draft', status: 'in_review' });

  it('renders the strip above the list view', () => {
    const { container } = renderList([inReview()], 'list');
    expect(container.querySelector('[data-testid="attention-strip"]')).not.toBeNull();
  });

  it('renders the same strip above the board view', () => {
    const { container } = renderList([inReview()], 'board');
    expect(container.querySelector('[data-testid="attention-strip"]')).not.toBeNull();
  });

  it('stays absent in both views when nothing is waiting', () => {
    const idle = () => mkIssue({ id: 21, identifier: 'MH-21', title: 'Plain', status: 'todo' });
    expect(renderList([idle()], 'list').container.querySelector('[data-testid="attention-strip"]')).toBeNull();
    expect(renderList([idle()], 'board').container.querySelector('[data-testid="attention-strip"]')).toBeNull();
  });
});


describe('IssueListView — Phase 分组 / 阶段芯片 / 行动作 (harness P4 T9)', () => {
  const running = () => mkIssue({ id: 30, identifier: 'MH-30', title: 'Running one', status: 'in_progress', raw: { dbos_workflow_id: 'wf-30', execution_state: { turn: 2 } } as never });
  const waiting = () => mkIssue({ id: 31, identifier: 'MH-31', title: 'Waiting one', status: 'needs_followup', raw: { execution_state: { agent_outcome: 'needs_input', outcome_reason: 'A or B?' } } as never });
  const paused = () => mkIssue({ id: 32, identifier: 'MH-32', title: 'Paused one', status: 'in_progress', raw: { paused_at: '2026-09-05T00:00:00Z' } as never });
  const plain = () => mkIssue({ id: 33, identifier: 'MH-33', title: 'Plain one', status: 'todo' });

  it('groups by phase by default, people-first order, and still offers Status / Project', () => {
    const { container } = renderList([plain(), running(), waiting(), paused()]);
    const groups = [...container.querySelectorAll('[data-testid="phase-group"]')].map((g) => g.getAttribute('data-phase'));
    expect(groups).toEqual(['waiting_input', 'running', 'paused', 'idle']);
    const toggle = container.querySelector('[data-testid="group-toggle"]')!;
    expect(toggle.textContent).toMatch(/Phase/);
    expect(toggle.textContent).toMatch(/Status/);
    expect(toggle.textContent).toMatch(/Project/);
    fireEvent.click(screen.getByText('Status'));
    expect(container.querySelector('[data-testid="phase-group"]')).toBeNull();
    expect(screen.getByText('Running one')).toBeTruthy();
  });

  it('quick phase chips carry counts, filter to one phase, and toggle off', () => {
    const { container } = renderList([plain(), running(), waiting(), paused()]);
    const chip = container.querySelector('[data-testid="quick-phase-running"]') as HTMLButtonElement;
    expect(chip.textContent).toMatch(/1/);
    expect((container.querySelector('[data-testid="quick-phase-blocked"]') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(chip);
    expect(screen.queryByText('Plain one')).toBeNull();
    expect(screen.getByText('Running one')).toBeTruthy();
    fireEvent.click(chip);
    expect(screen.getByText('Plain one')).toBeTruthy();
  });

  it('row action names the verb for the phase — Reply when waiting, Steer when running, nothing when idle', () => {
    const { container } = renderList([plain(), running(), waiting()]);
    const rows = [...container.querySelectorAll('a[data-phase]')];
    const actionOf = (phase: string) => rows.find((r) => r.getAttribute('data-phase') === phase)?.querySelector('[data-testid="row-action"]')?.textContent ?? null;
    expect(actionOf('waiting_input')).toBe('Reply');
    expect(actionOf('running')).toBe('Steer');
    expect(actionOf('idle')).toBeNull();
  });

  it('paused issues join the waiting-on-you strip and the selected row is marked', () => {
    const { container } = renderList([paused(), plain()], 'list', 33);
    expect(container.querySelector('[data-testid="attention-strip"]')?.textContent).toMatch(/Paused one/);
    const selected = container.querySelector('a[aria-current="true"]');
    expect(selected?.textContent).toMatch(/Plain one/);
  });
});

describe('IssueListView — paused rows (phase 2a §4)', () => {
  it('offers Resume as the row action on a paused issue and shows the queued count', async () => {
    fetchPendingSummary.mockResolvedValueOnce({ '5': { count: 2, oldestAt: '2026-09-08T00:00:00Z' } });
    renderList([
      mkIssue({
        id: 5, identifier: 'MH-5', title: 'Paused with mail', status: 'in_progress',
        raw: { dbos_workflow_id: 'wf-5', paused_at: '2026-09-08T00:00:00Z' } as Issue,
      }),
    ]);
    // The attention strip also names the issue (paused card) — pick the list row.
    const row = screen.getAllByText('Paused with mail').map((el) => el.closest('a[data-phase]')).find(Boolean) as HTMLElement;
    expect(row.getAttribute('data-phase')).toBe('paused');
    expect(row.querySelector('[data-testid="row-action"]')!.textContent).toBe('Resume');
    const chip = await screen.findByTestId('queued-chip');
    expect(chip.textContent).toBe('2 queued');
  });

  it('draws no queued chip when nothing is waiting', async () => {
    fetchPendingSummary.mockResolvedValueOnce({ '5': { count: 0, oldestAt: '2026-09-08T00:00:00Z' } });
    renderList([
      mkIssue({
        id: 5, identifier: 'MH-5', title: 'Paused, quiet', status: 'in_progress',
        raw: { dbos_workflow_id: 'wf-5', paused_at: '2026-09-08T00:00:00Z' } as Issue,
      }),
    ]);
    await screen.findAllByText('Paused, quiet');
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('queued-chip')).toBeNull();
  });
});
