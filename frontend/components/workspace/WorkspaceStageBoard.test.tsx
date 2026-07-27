/**
 * WorkspaceStageBoard (M2 PR-F F3) — the Stage Board workspace module.
 *
 * Pins: the three-section layout (node header / Tasks / Deliverables) all
 * render off one `fetchStageBoard` call; a `null` mirror issue reads as the
 * "No mirror issue yet" empty state instead of crashing; the bottom action bar
 * ("Complete Stage") only renders for a node in the workflow's active group
 * (current node ± its parallel-group siblings) — every other node renders no
 * action bar at all, never a disabled one; an overdue node (past `planned_due`,
 * not done/skipped) gets the same rose "Overdue" mark as CurrentNodeCard.
 */
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { WorkspaceStageBoard } from './WorkspaceStageBoard';
import type { ProjectStageNode, ProjectWorkflow, StageBoardData } from '../../types';

function makeI18n(): I18n {
  const instance = createInstance();
  instance.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return instance;
}

const mockWorkflowService = vi.hoisted(() => ({
  fetchStageBoard: vi.fn(),
}));
vi.mock('../../services/workflowService', () => mockWorkflowService);

// Owner/agent name resolution pools — same shape WorkflowSection fetches for
// CurrentNodeCard's OwnerPicker (#final-review: WorkspaceStageBoard used to
// render the raw owner_user_id/owner_agent_id UUIDs instead of resolving them).
const mockProjectsService = vi.hoisted(() => ({
  fetchProjectMembers: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockProjectsService);

const mockAiLibraryService = vi.hoisted(() => ({
  aiLibraryService: { listAgents: vi.fn() },
}));
vi.mock('../../services/aiLibraryService', () => mockAiLibraryService);

const mockDeliverablesZone = vi.hoisted(() => vi.fn());
vi.mock('../Todolist/DeliverablesZone', () => ({
  DeliverablesZone: (props: {
    projectId: string;
    issueId: number;
    isStageMirror: boolean;
    projectName: string;
    stageName?: string;
  }) => {
    mockDeliverablesZone(props);
    return <div data-testid="deliverables-zone-stub" />;
  },
}));

// H3: Run now opens the real DispatchConfirmDialog, prefetching a preview and
// (on confirm) dispatching — both hit issuesService, which must be mocked out
// here the same way it is in IssueDetailView's own test suite.
const mockIssuesService = vi.hoisted(() => ({
  getDispatchPreview: vi.fn(),
  dispatchIssue: vi.fn(),
}));
vi.mock('../../services/issuesService', () => mockIssuesService);

function node(over: Partial<ProjectStageNode>): ProjectStageNode {
  return {
    id: '1',
    project_id: '10',
    source_template_node_id: null,
    legacy_stage_id: null,
    name: 'Script',
    sort_order: 0,
    parallel_group: null,
    status: 'in_progress',
    owner_user_id: null,
    owner_agent_id: null,
    planned_start: null,
    planned_due: null,
    review_required: false,
    deliverable_required: false,
    deliverable_label: null,
    skipped: false,
    folder_id: null,
    deliverable_file_count: 0,
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    ...over,
  };
}

function board(over: Partial<StageBoardData> = {}): StageBoardData {
  return {
    node: node({}),
    issue: null,
    files: [],
    ...over,
  };
}

function workflow(over: Partial<ProjectWorkflow> = {}): ProjectWorkflow {
  return {
    has_workflow: true,
    current_node_id: '1',
    agents_active: 0,
    nodes: [node({})],
    ...over,
  };
}

function renderBoard(
  data: StageBoardData,
  opts: {
    workflow?: ProjectWorkflow | null;
    canWrite?: boolean;
    onRequestAdvance?: (direction: 'forward' | 'back') => void;
    onOpenTodolist?: () => void;
  } = {},
) {
  mockWorkflowService.fetchStageBoard.mockResolvedValue(data);
  const onRequestAdvance = opts.onRequestAdvance ?? vi.fn();
  const onOpenTodolist = opts.onOpenTodolist ?? vi.fn();
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <WorkspaceStageBoard
        projectId="10"
        projectName="Spring Campaign"
        nodeId={data.node.id}
        workflow={opts.workflow === undefined ? workflow() : opts.workflow}
        canWrite={opts.canWrite ?? true}
        onRequestAdvance={onRequestAdvance}
        onOpenTodolist={onOpenTodolist}
      />
    </I18nextProvider>,
  );
  return { ...utils, onRequestAdvance, onOpenTodolist };
}

beforeEach(() => {
  mockWorkflowService.fetchStageBoard.mockReset();
  mockDeliverablesZone.mockClear();
  mockProjectsService.fetchProjectMembers.mockReset().mockResolvedValue([]);
  mockAiLibraryService.aiLibraryService.listAgents.mockReset().mockResolvedValue([]);
  mockIssuesService.getDispatchPreview.mockReset();
  mockIssuesService.dispatchIssue.mockReset();
});

afterEach(() => {
  cleanup();
});

describe('WorkspaceStageBoard', () => {
  it('renders the three-section layout: node header, Tasks, Deliverables', async () => {
    renderBoard(
      board({
        node: node({ name: 'Storyboard', status: 'in_progress' }),
        issue: {
          id: '100',
          identifier: 'ENG-42',
          title: 'Storyboard mirror issue',
          status: 'in_progress',
          assignee: { user_id: null, agent_id: null },
          sub_issues: [
            {
              id: '101',
              identifier: 'ENG-43',
              title: 'Shot 1',
              status: 'todo',
              assignee: { user_id: null, agent_id: null },
            },
          ],
        },
        files: [
          { id: 'f1', filename: 'final_cut.mp4', size: 1024, created_at: '2026-07-01T00:00:00Z', source_issue_identifier: null },
        ],
      }),
    );

    await waitFor(() => expect(mockWorkflowService.fetchStageBoard).toHaveBeenCalledWith('10', '1'));

    expect(await screen.findByTestId('workspace-stage-board')).toBeInTheDocument();
    // Node header
    const header = screen.getByTestId('stage-board-header');
    expect(header).toHaveTextContent('Storyboard');
    // Tasks section — mirror issue + sub-issue, both read-only rows
    const tasks = screen.getByTestId('stage-board-tasks');
    expect(tasks).toHaveTextContent('ENG-42');
    expect(tasks).toHaveTextContent('Storyboard mirror issue');
    expect(tasks).toHaveTextContent('ENG-43');
    expect(tasks).toHaveTextContent('Shot 1');
    expect(screen.getByTestId('stage-board-open-todolist')).toBeInTheDocument();
    // Deliverables section — no folder_id on the node → read-only file list,
    // not the live DeliverablesZone dropzone.
    const deliverables = screen.getByTestId('stage-board-deliverables');
    expect(deliverables).toHaveTextContent('final_cut.mp4');
    expect(screen.queryByTestId('deliverables-zone-stub')).toBeNull();
  });

  it('renders DeliverablesZone when the node has a mirror issue and a folder', async () => {
    renderBoard(
      board({
        node: node({ name: 'Storyboard', folder_id: 'folder-1' }),
        issue: {
          id: '100',
          identifier: 'ENG-42',
          title: 'Storyboard mirror issue',
          status: 'in_progress',
          assignee: { user_id: null, agent_id: null },
          sub_issues: [],
        },
      }),
    );

    expect(await screen.findByTestId('deliverables-zone-stub')).toBeInTheDocument();
    expect(mockDeliverablesZone).toHaveBeenCalledWith(
      expect.objectContaining({ issueId: 100, isStageMirror: true, projectName: 'Spring Campaign' }),
    );
  });

  it('shows the "No mirror issue yet" empty state when issue is null', async () => {
    renderBoard(board({ issue: null }));

    expect(await screen.findByTestId('stage-board-no-issue')).toHaveTextContent(
      'No mirror issue yet',
    );
    // Open in Todolist is still a plain navigation shortcut, not gated on an issue existing.
    expect(screen.getByTestId('stage-board-open-todolist')).toBeInTheDocument();
  });

  it('renders no action bar for a node outside the active group', async () => {
    renderBoard(
      board({ node: node({ id: '2', parallel_group: null }) }),
      { workflow: workflow({ current_node_id: '1', nodes: [node({ id: '1' }), node({ id: '2' })] }) },
    );

    await screen.findByTestId('workspace-stage-board');
    expect(screen.queryByTestId('stage-board-actions')).toBeNull();
    expect(screen.queryByTestId('stage-board-complete')).toBeNull();
  });

  it('renders the Complete Stage action for the current active node', async () => {
    const onRequestAdvance = vi.fn();
    renderBoard(
      board({ node: node({ id: '1' }) }),
      {
        workflow: workflow({ current_node_id: '1', nodes: [node({ id: '1' })] }),
        onRequestAdvance,
      },
    );

    const btn = await screen.findByTestId('stage-board-complete');
    fireEvent.click(btn);
    expect(onRequestAdvance).toHaveBeenCalledWith('forward');
  });

  it('renders the Complete Stage action for a parallel-group sibling of the active node', async () => {
    renderBoard(
      board({ node: node({ id: '2', parallel_group: 1 }) }),
      {
        workflow: workflow({
          current_node_id: '1',
          nodes: [node({ id: '1', parallel_group: 1 }), node({ id: '2', parallel_group: 1 })],
        }),
      },
    );

    expect(await screen.findByTestId('stage-board-complete')).toBeInTheDocument();
  });

  it('marks an overdue node with the rose Overdue chip', async () => {
    renderBoard(
      board({
        node: node({
          status: 'in_progress',
          planned_due: '2020-01-01',
        }),
      }),
    );

    expect(await screen.findByTestId('stage-board-overdue')).toHaveTextContent('Overdue');
  });

  it('does not mark a done node overdue even with a past planned_due', async () => {
    renderBoard(
      board({
        node: node({ status: 'done', planned_due: '2020-01-01' }),
      }),
    );

    await screen.findByTestId('workspace-stage-board');
    expect(screen.queryByTestId('stage-board-overdue')).toBeNull();
  });

  // M3 PR-J (task J3): "Waiting on" row — local derivation off the shared
  // `workflow` instance's node list (nodeStatus.ts::unmetDeps), display-only
  // (the server's DEPS_PENDING predicate is the actual advance gate).
  it('shows a "Waiting on" row when a dependency is not yet done', async () => {
    const dep = node({ id: 'dep-1', name: 'Script', status: 'pending' });
    renderBoard(
      board({ node: node({ id: '1', name: 'Storyboard', depends_on: ['dep-1'] }) }),
      { workflow: workflow({ current_node_id: '1', nodes: [dep, node({ id: '1', name: 'Storyboard', depends_on: ['dep-1'] })] }) },
    );

    const row = await screen.findByTestId('stage-board-waiting-on');
    expect(row).toHaveTextContent('Script');
  });

  it('renders no "Waiting on" row once the dependency is done', async () => {
    const dep = node({ id: 'dep-1', name: 'Script', status: 'done' });
    renderBoard(
      board({ node: node({ id: '1', name: 'Storyboard', depends_on: ['dep-1'] }) }),
      { workflow: workflow({ current_node_id: '1', nodes: [dep, node({ id: '1', name: 'Storyboard', depends_on: ['dep-1'] })] }) },
    );

    await screen.findByTestId('workspace-stage-board');
    expect(screen.queryByTestId('stage-board-waiting-on')).toBeNull();
  });

  it('renders no "Waiting on" row when the node has no depends_on', async () => {
    renderBoard(board({ node: node({ id: '1', name: 'Storyboard' }) }));

    await screen.findByTestId('workspace-stage-board');
    expect(screen.queryByTestId('stage-board-waiting-on')).toBeNull();
  });

  it('resolves owner_agent_id / owner_user_id to display names instead of raw UUIDs', async () => {
    mockAiLibraryService.aiLibraryService.listAgents.mockResolvedValue([
      { id: 'agent-uuid-1', slug: 'script-ai', name: 'Script AI' },
    ]);

    renderBoard(
      board({
        node: node({
          owner_agent_id: 'agent-uuid-1',
          events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: true },
        }),
      }),
    );

    const owner = await screen.findByTestId('stage-board-owner');
    await waitFor(() => expect(owner).toHaveTextContent('Script AI'));
    expect(owner).not.toHaveTextContent('agent-uuid-1');

    const chip = screen.getByTestId('stage-board-suggest-chip');
    expect(chip).toHaveTextContent('Script AI');
    expect(chip).not.toHaveTextContent('agent-uuid-1');
  });

  it('resolves owner_user_id to the project member name', async () => {
    mockProjectsService.fetchProjectMembers.mockResolvedValue([
      {
        project_id: '10',
        user_id: 'user-uuid-1',
        email: 'jamie@example.com',
        role: 'editor',
        invited_by: null,
        joined_at: '2026-01-01T00:00:00Z',
      },
    ]);

    renderBoard(board({ node: node({ owner_user_id: 'user-uuid-1' }) }));

    const owner = await screen.findByTestId('stage-board-owner');
    await waitFor(() => expect(owner).toHaveTextContent('jamie@example.com'));
    expect(owner).not.toHaveTextContent('user-uuid-1');
  });

  it('falls back to a generic label — never the raw id — when the agent is not in the candidate pool', async () => {
    renderBoard(
      board({
        node: node({
          owner_agent_id: 'agent-unknown',
          events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: true },
        }),
      }),
    );

    const chip = await screen.findByTestId('stage-board-suggest-chip');
    expect(chip).not.toHaveTextContent('agent-unknown');
    expect(chip).toHaveTextContent('Agent');
  });

  it('refetches the board when the workflow instance changes (e.g. after a Complete Stage advance)', async () => {
    const wf1 = workflow({ current_node_id: '1' });
    const { rerender } = renderBoard(board({ node: node({ id: '1', status: 'in_progress' }) }), {
      workflow: wf1,
    });

    await waitFor(() => expect(mockWorkflowService.fetchStageBoard).toHaveBeenCalledTimes(1));

    // Same projectId/nodeId, but a NEW workflow object (as reloadWorkflow()
    // would produce post-advance) — must trigger a refetch so stale
    // header/status/tasks don't linger (#final-review board-refresh item).
    mockWorkflowService.fetchStageBoard.mockResolvedValue(
      board({ node: node({ id: '1', status: 'done' }) }),
    );
    const wf2 = workflow({ current_node_id: '2', nodes: [node({ id: '1', status: 'done' }), node({ id: '2' })] });
    rerender(
      <I18nextProvider i18n={makeI18n()}>
        <WorkspaceStageBoard
          projectId="10"
          projectName="Spring Campaign"
          nodeId="1"
          workflow={wf2}
          canWrite
          onRequestAdvance={vi.fn()}
          onOpenTodolist={vi.fn()}
        />
      </I18nextProvider>,
    );

    await waitFor(() => expect(mockWorkflowService.fetchStageBoard).toHaveBeenCalledTimes(2));
  });
});

describe('WorkspaceStageBoard — Run now chip (M3 Task H3)', () => {
  const issue = {
    id: '100',
    identifier: 'ENG-42',
    title: 'Storyboard mirror issue',
    status: 'in_progress',
    assignee: { user_id: null, agent_id: null },
    sub_issues: [],
  };

  it('renders the solid Run now button instead of the suggest chip once metadata.run_prepared_at is set', async () => {
    renderBoard(
      board({
        node: node({
          owner_agent_id: 'agent-1',
          events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: true },
          metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
        }),
        issue,
      }),
    );

    expect(await screen.findByTestId('stage-board-run-now')).toHaveTextContent('Run now');
    expect(screen.queryByTestId('stage-board-suggest-chip')).toBeNull();
  });

  it('keeps the plain suggest chip when the hook has not fired yet (no run_prepared_at)', async () => {
    renderBoard(
      board({
        node: node({
          owner_agent_id: 'agent-1',
          events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: true },
        }),
        issue,
      }),
    );

    expect(await screen.findByTestId('stage-board-suggest-chip')).toBeInTheDocument();
    expect(screen.queryByTestId('stage-board-run-now')).toBeNull();
  });

  it('clicking Run now opens DispatchConfirmDialog prefetched with a preview, and confirming calls dispatchIssue — never dispatches on click alone', async () => {
    mockIssuesService.getDispatchPreview.mockResolvedValue({
      will_start: true,
      agent_id: 'agent-1',
      blocked_reason: null,
    });
    mockIssuesService.dispatchIssue.mockResolvedValue({});
    mockAiLibraryService.aiLibraryService.listAgents.mockResolvedValue([
      { id: 'agent-1', slug: 'script-ai', name: 'Script Bot' },
    ]);

    renderBoard(
      board({
        node: node({
          owner_agent_id: 'agent-1',
          events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: true },
          metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
        }),
        issue,
      }),
    );

    fireEvent.click(await screen.findByTestId('stage-board-run-now'));

    // The dialog is open and asking the server what a dispatch would start —
    // dispatch itself must NOT have been called yet.
    const dialog = await screen.findByRole('dialog', { name: 'Confirm dispatch' });
    await waitFor(() => expect(mockIssuesService.getDispatchPreview).toHaveBeenCalledWith(100));
    expect(mockIssuesService.dispatchIssue).not.toHaveBeenCalled();
    // "Script Bot" also appears in the header's Owner line — scope to the
    // dialog so this doesn't collide with that other, unrelated match.
    await within(dialog).findByText(/Script Bot/);

    fireEvent.click(within(dialog).getByText('Start working'));

    await waitFor(() => expect(mockIssuesService.dispatchIssue).toHaveBeenCalledWith(100));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Confirm dispatch' })).toBeNull());
  });

  it('falls back to Open in Todolist when Run now is clicked but there is no mirror issue to dispatch', async () => {
    const onOpenTodolist = vi.fn();
    renderBoard(
      board({
        node: node({
          owner_agent_id: 'agent-1',
          events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: true },
          metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
        }),
        issue: null,
      }),
      { onOpenTodolist },
    );

    fireEvent.click(await screen.findByTestId('stage-board-run-now'));

    expect(onOpenTodolist).toHaveBeenCalledTimes(1);
    expect(mockIssuesService.getDispatchPreview).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: 'Confirm dispatch' })).toBeNull();
  });
});
