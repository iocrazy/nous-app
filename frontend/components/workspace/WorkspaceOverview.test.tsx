/**
 * WorkspaceOverview (IA redesign Task 4 rewrite, `2026-08-10-workspace-ia-redesign`)
 * — the workspace landing module as an episode accordion.
 *
 * Pins: one row per episode (no Continue card / summary-tile grid / surface
 * panel — those are gone); the row addressed by `expandedEpisodeId` (URL
 * `ep=`) expands to show its workflow strip + the Task 5 node-card slot
 * (`renderNodeCard`); clicking an already-open row collapses it
 * (`onExpandEpisode(null)`) without picking a different episode; the
 * no-workflow empty state still reaches `WorkflowSection`'s attach CTA
 * (ambiguity #1); the recent-activity line is unchanged.
 */
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';

import { WorkspaceOverview } from './WorkspaceOverview';
import { ToastProvider } from '../Toast';
import type { EpisodeProgress, Project, ProjectWorkflow } from '../../types';

// relativeTime pulls in the real i18n instance via formatDate — stub it so
// this suite doesn't need initReactI18next (mirrors StageWorkbench.test.tsx).
vi.mock('../../utils/relativeTime', () => ({
  formatRelativeTime: () => '2h ago',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: unknown) => {
      if (key.startsWith('projects.workspace.modules.')) return key.split('.').pop()!;
      if (key.startsWith('projects.workspace.episodeStatus.')) return key.split('.').pop()!;
      if (key === 'projects.card.activityFile') return 'Added files';
      if (key === 'projects.workspace.overview.awaiting')
        return `${(opts as { count?: number } | undefined)?.count ?? 0} awaiting you`;
      return key;
    },
  }),
}));

// WorkflowSection (mounted for the no-workflow attach-CTA test) fetches
// members/agents unconditionally on mount and reads useToast() — stub both
// so that render path doesn't hit real network/context. Same pattern as
// WorkflowSection.test.tsx.
const mockWorkflowService = vi.hoisted(() => ({
  addProjectNode: vi.fn(),
  deleteProjectNode: vi.fn(),
  fetchStageLibrary: vi.fn().mockResolvedValue([]),
  startEarlyNode: vi.fn(),
  fetchTemplates: vi.fn(),
  attachProjectWorkflow: vi.fn(),
}));
vi.mock('../../services/workflowService', () => mockWorkflowService);

const mockProjectsService = vi.hoisted(() => ({
  fetchProjectMembers: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../services/projectsService', () => mockProjectsService);

const mockAiLibraryService = vi.hoisted(() => ({
  aiLibraryService: { listAgents: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../../services/aiLibraryService', () => mockAiLibraryService);

const PROJECT: Project = {
  id: 'p1',
  name: 'Spring Campaign',
  description: null,
  owner_id: 'u1',
  team_id: 't1',
  project_type: 'internal',
  project_group: null,
  announcement: null,
  is_starred: false,
  color_label: null,
  archived_at: null,
  file_count: 128,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  latest_activity: { kind: 'file', actor: 'Alice', at: '2026-07-08T00:00:00Z', stalled: false },
};

const EPISODES: EpisodeProgress[] = [
  {
    episode_id: 'ep1',
    title: 'Ep 1 — Pilot',
    sort_order: 10,
    script_count: 1,
    scene_count: 4,
    shots_total: 12,
    shots_done: 9,
    renders_count: 1,
    status: 'boarding',
  },
  {
    episode_id: 'ep2',
    title: 'Ep 2 — Cutdown',
    sort_order: 20,
    script_count: 1,
    scene_count: 2,
    shots_total: 4,
    shots_done: 0,
    renders_count: 0,
    status: 'drafting',
  },
  {
    episode_id: 'ep3',
    title: 'Ep 3 — Turn',
    sort_order: 30,
    script_count: 1,
    scene_count: 3,
    shots_total: 6,
    shots_done: 2,
    renders_count: 0,
    status: 'planned',
  },
  {
    episode_id: 'ep4',
    title: 'Ep 4 — Finale',
    sort_order: 40,
    script_count: 1,
    scene_count: 5,
    shots_total: 8,
    shots_done: 8,
    renders_count: 2,
    status: 'rendered',
  },
];

const noop = () => {};

const base = {
  project: PROJECT,
  episodes: EPISODES,
  workflow: null as ProjectWorkflow | null,
  workflowLoading: false,
  expandedEpisodeId: null as string | null,
  selectedNodeId: null as string | null,
  onExpandEpisode: noop,
  onSelectNode: noop,
  renderNodeCard: (episodeId: string, nodeId: string | null) => (
    <div data-testid="node-card-slot">
      CARD:{episodeId}:{nodeId}
    </div>
  ),
};

function renderOverview(props: Partial<typeof base> = {}) {
  return render(
    <ToastProvider>
      <WorkspaceOverview {...base} {...props} />
    </ToastProvider>,
  );
}

afterEach(() => cleanup());

describe('WorkspaceOverview', () => {
  it('renders one row per episode and no summary tiles / continue card / surface panel', () => {
    renderOverview();
    expect(screen.getAllByTestId(/^ep-accordion-row-/)).toHaveLength(4);
    expect(screen.queryByTestId('ws-overview-summary')).toBeNull();
    expect(screen.queryByTestId('ws-continue-card')).toBeNull();
    expect(screen.queryByTestId('episode-surface-panel')).toBeNull();
  });

  it('expanded row shows that episode strip and node card slot; others collapsed', () => {
    renderOverview({ expandedEpisodeId: 'ep1', selectedNodeId: 'n2' });
    expect(screen.getByTestId('ep-accordion-body-ep1')).toBeTruthy();
    expect(screen.queryByTestId('ep-accordion-body-ep2')).toBeNull();
    expect(screen.getByTestId('node-card-slot').textContent).toContain('CARD:ep1:n2');
  });

  it('clicking an expanded row collapses it (onExpandEpisode null)', () => {
    const onExpandEpisode = vi.fn();
    renderOverview({ expandedEpisodeId: 'ep1', onExpandEpisode });
    fireEvent.click(screen.getByTestId('ep-accordion-row-ep1'));
    expect(onExpandEpisode).toHaveBeenCalledWith(null);
  });

  it('clicking a closed row opens it (onExpandEpisode with that episode id)', () => {
    const onExpandEpisode = vi.fn();
    renderOverview({ expandedEpisodeId: 'ep1', onExpandEpisode });
    fireEvent.click(screen.getByTestId('ep-accordion-row-ep2'));
    expect(onExpandEpisode).toHaveBeenCalledWith('ep2');
  });

  // ── Task 4 修复轮1: stale-episode workflow flash guard ──────────────────
  // `useProjectWorkflow` does NOT clear its `workflow` state on a
  // truthy→truthy episodeId change (only `loading` flips back to true) — so
  // right after switching the expanded episode, `workflow` can still hold
  // the PREVIOUS episode's nodes/current_node_id while `workflowLoading` is
  // true. Simulates exactly that window: `expandedEpisodeId` already points
  // at ep2, but `workflow` carries stale data (a node id that isn't even in
  // ep2's node list, standing in for "ep1's current_node_id").

  it('gates the expanded row on workflowLoading — no stale strip while a switch is in flight', () => {
    renderOverview({
      expandedEpisodeId: 'ep2',
      workflowLoading: true,
      workflow: {
        has_workflow: true,
        current_node_id: 'stale-node-from-ep1',
        agents_active: 0,
        nodes: [],
      },
    });
    expect(screen.getByTestId('ep-accordion-loading-ep2')).toBeTruthy();
    expect(screen.queryByTestId('workflow-strip')).toBeNull();
  });

  it('does not call renderNodeCard with a stale current_node_id while workflowLoading is true', () => {
    const renderNodeCard = vi.fn((episodeId: string, nodeId: string | null) => (
      <div data-testid="node-card-slot">
        CARD:{episodeId}:{nodeId}
      </div>
    ));
    renderOverview({
      expandedEpisodeId: 'ep2',
      selectedNodeId: null,
      workflowLoading: true,
      workflow: {
        has_workflow: true,
        current_node_id: 'stale-node-from-ep1',
        agents_active: 0,
        nodes: [],
      },
      renderNodeCard,
    });
    expect(renderNodeCard).not.toHaveBeenCalled();
    expect(screen.queryByTestId('node-card-slot')).toBeNull();
  });

  it('renders the real strip/card slot (not the loading placeholder) once workflowLoading flips back to false', () => {
    const { rerender } = render(
      <ToastProvider>
        <WorkspaceOverview
          {...base}
          expandedEpisodeId="ep2"
          selectedNodeId="n9"
          workflowLoading
          workflow={{ has_workflow: true, current_node_id: 'stale-node-from-ep1', agents_active: 0, nodes: [] }}
        />
      </ToastProvider>,
    );
    expect(screen.getByTestId('ep-accordion-loading-ep2')).toBeTruthy();

    rerender(
      <ToastProvider>
        <WorkspaceOverview
          {...base}
          expandedEpisodeId="ep2"
          selectedNodeId="n9"
          workflowLoading={false}
          workflow={{ has_workflow: true, current_node_id: 'n9', agents_active: 0, nodes: [] }}
        />
      </ToastProvider>,
    );
    expect(screen.queryByTestId('ep-accordion-loading-ep2')).toBeNull();
    expect(screen.getByTestId('node-card-slot').textContent).toContain('CARD:ep2:n9');
  });

  // ── Ambiguity #1: no-workflow projects keep the attach entry reachable ──

  it('renders the WorkflowSection attach entry when no workflow is attached (has_workflow=false)', () => {
    renderOverview({
      workflow: { has_workflow: false, current_node_id: null, agents_active: 0, nodes: [] },
    });
    expect(screen.getByTestId('workflow-empty-state')).toBeTruthy();
    expect(screen.getByTestId('workflow-empty-state-attach')).toBeTruthy();
  });

  it('does not render the attach entry while workflow is still loading (null)', () => {
    renderOverview({ workflow: null });
    expect(screen.queryByTestId('workflow-empty-state')).toBeNull();
  });

  it('does not render the attach entry once a workflow is attached (has_workflow=true)', () => {
    renderOverview({
      workflow: { has_workflow: true, current_node_id: 'n1', agents_active: 0, nodes: [] },
    });
    expect(screen.queryByTestId('workflow-empty-state')).toBeNull();
  });

  // Task 4 修复轮2: `has_workflow` is PER-EPISODE (backend `projects_router`
  // computes it per `episode_id`), so it's a SECOND consumer of the same
  // stale-data window as the accordion body (修复轮1) — switching from a
  // no-workflow episode to a workflow-attached one can hold the OLD episode's
  // `has_workflow: false` in `workflow` while the new fetch is in flight,
  // which would flash the attach CTA under the newly-expanded (attached)
  // episode for one render.
  it('does not render the attach entry while workflowLoading is true, even with a stale has_workflow=false', () => {
    renderOverview({
      expandedEpisodeId: 'ep2',
      workflowLoading: true,
      workflow: { has_workflow: false, current_node_id: null, agents_active: 0, nodes: [] },
    });
    expect(screen.queryByTestId('workflow-empty-state')).toBeNull();
  });

  it('shows the awaiting hint only for episodes parked at the planned stage', () => {
    const withPlanned: EpisodeProgress[] = [
      { ...EPISODES[0], status: 'planned' },
      { ...EPISODES[1], episode_id: 'ep5', status: 'planned' },
      EPISODES[1],
    ];
    const { rerender } = render(
      <ToastProvider>
        <WorkspaceOverview {...base} episodes={withPlanned} />
      </ToastProvider>,
    );
    expect(screen.getByTestId('ws-rollup-awaiting')).toHaveTextContent('2 awaiting you');

    // No planned episodes → hint hidden.
    const noPlanned = EPISODES.filter((e) => e.status !== 'planned');
    rerender(
      <ToastProvider>
        <WorkspaceOverview {...base} episodes={noPlanned} />
      </ToastProvider>,
    );
    expect(screen.queryByTestId('ws-rollup-awaiting')).toBeNull();
  });

  it('counts real needs_input episodes when the workflow rollup is present', () => {
    // B4 真数据:两集各有 agent 提问(needs_input_count>0)但都不在 planned —
    // 旧的 planned 近似会显示 0(隐藏),真数据应显示 2。
    const wf = (needs: number) => ({
      nodes_total: 8,
      nodes_done: 1,
      current_node_id: null,
      needs_input_count: needs,
    });
    const withQuestions: EpisodeProgress[] = [
      { ...EPISODES[0], workflow: wf(2) },
      { ...EPISODES[1], workflow: wf(1) },
    ];
    const { rerender } = render(
      <ToastProvider>
        <WorkspaceOverview {...base} episodes={withQuestions} />
      </ToastProvider>,
    );
    expect(screen.getByTestId('ws-rollup-awaiting')).toHaveTextContent('2 awaiting you');

    // 反向:有 workflow 信号时,停在 planned 但没有提问的集不再被误计。
    const plannedNoQuestions: EpisodeProgress[] = [
      { ...EPISODES[0], status: 'planned', workflow: wf(0) },
      { ...EPISODES[1], workflow: wf(0) },
    ];
    rerender(
      <ToastProvider>
        <WorkspaceOverview {...base} episodes={plannedNoQuestions} />
      </ToastProvider>,
    );
    expect(screen.queryByTestId('ws-rollup-awaiting')).toBeNull();
  });

  it('renders the recent-activity line from project.latest_activity', () => {
    renderOverview();
    expect(screen.getByTestId('ws-overview-activity')).toHaveTextContent('Added files');
    expect(screen.getByTestId('ws-overview-activity')).toHaveTextContent('Alice');
  });

  it('renders no rows when there are no episodes', () => {
    renderOverview({ episodes: [] });
    expect(screen.queryAllByTestId(/^ep-accordion-row-/)).toHaveLength(0);
  });
});
