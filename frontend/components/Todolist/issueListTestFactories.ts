/**
 * Shared fixtures for IssueListView tests (3c Task 15).
 *
 * `uiIssue` builds the **UiIssue** shape — what `toUiIssue` already produced,
 * so `id` is a number, matching `components/Todolist/types.ts`. That is NOT the
 * wire shape: a mock of the HTTP response body would have to copy whatever
 * `/api/v1/issues` really returns (see `e2e/helpers/realShapes.ts`). Here the
 * boundary being faked is a React prop, so the prop's own type is the right
 * one.
 */
import type { ComponentProps } from 'react';
import { vi } from 'vitest';

import { IssueListView } from './IssueListView';
import type { UiIssue } from './types';
import type { Issue } from '../../services/issuesService';

type Props = ComponentProps<typeof IssueListView>;

/** One row, UiIssue shape. `raw` carries the untouched server row. */
export function uiIssue(over: Partial<UiIssue> = {}): UiIssue {
  const id = over.id ?? 96;
  const identifier = over.identifier ?? 'MH-96';
  const title = over.title ?? 'Alpha';
  const status = over.status ?? 'backlog';
  const raw = {
    id,
    identifier,
    title,
    status,
    priority: over.priority ?? 'medium',
    project_id: null,
    assignee_user_id: null,
    assignee_agent_id: null,
    created_by_user_id: null,
    created_by_agent_id: null,
    dbos_workflow_id: null,
    origin_kind: 'manual',
    parent_id: null,
  } as unknown as Issue;
  return {
    id,
    identifier,
    title,
    description: null,
    status,
    priority: over.priority ?? 'medium',
    parent_id: null,
    created_at: '2026-09-15T00:00:00Z',
    updated_at: '2026-09-15T00:00:00Z',
    last_activity_at: '2026-09-15T00:00:00Z',
    raw,
    ...over,
  } as UiIssue;
}

/** Every required prop of IssueListView, with inert callbacks. */
export function baseProps(): Props {
  return {
    issues: [],
    loading: false,
    error: null,
    viewMode: 'list',
    onViewModeChange: vi.fn(),
    onNewIssue: vi.fn(),
    onRefresh: vi.fn(),
    agents: [],
    scope: { type: 'team', teamId: '7' },
  };
}
