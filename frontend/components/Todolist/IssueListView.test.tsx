import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
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

function renderList(issues: UiIssue[]) {
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
              viewMode="list"
              onViewModeChange={vi.fn()}
              onNewIssue={vi.fn()}
              onRefresh={vi.fn()}
              agents={[]}
              scope={{ type: 'team', teamId: '8' }}
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
