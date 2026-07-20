/**
 * loadProjectFlow — W2-2 single-point flow source for the issue context bar.
 * A workflow project reads its node chain; everyone else falls back to the SOP
 * catalog. These tests drive both branches with mocked services.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/workflowService', () => ({
  fetchProjectWorkflow: vi.fn(),
}));
vi.mock('../../services/projectsService', () => ({
  fetchCurrentStage: vi.fn(),
  fetchStageCatalog: vi.fn(),
}));

import {
  deriveSopFlow,
  deriveWorkflowFlow,
  loadProjectFlow,
} from './issueFlow';
import { fetchProjectWorkflow } from '../../services/workflowService';
import { fetchCurrentStage, fetchStageCatalog } from '../../services/projectsService';

const mockWorkflow = fetchProjectWorkflow as unknown as ReturnType<typeof vi.fn>;
const mockCurrent = fetchCurrentStage as unknown as ReturnType<typeof vi.fn>;
const mockCatalog = fetchStageCatalog as unknown as ReturnType<typeof vi.fn>;

function node(id: string, name: string, sort_order: number) {
  return {
    id,
    project_id: '1',
    source_template_node_id: null,
    legacy_stage_id: null,
    name,
    sort_order,
    parallel_group: null,
    status: 'pending' as const,
    owner_user_id: null,
    owner_agent_id: null,
    planned_start: null,
    planned_due: null,
    review_required: false,
    deliverable_required: false,
    deliverable_label: null,
    skipped: false,
    members: [],
  };
}

function stage(id: string, name: string, sort_order: number) {
  return { id, slug: name.toLowerCase(), name, sort_order, tools_recommended: [] };
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ── pure derivations ──────────────────────────────────────────────────────────

describe('deriveWorkflowFlow', () => {
  it('maps the current node to a 1-based ring over the full node list', () => {
    const wf = {
      has_workflow: true,
      current_node_id: 'n2',
      agents_active: 0,
      nodes: [node('n1', 'Script', 10), node('n2', 'Storyboard', 20), node('n3', 'Canvas', 30)],
    };
    expect(deriveWorkflowFlow(wf)).toEqual({ name: 'Storyboard', index: 2, total: 3 });
  });

  it('is null when there is no workflow, no nodes, or no resolvable current node', () => {
    expect(deriveWorkflowFlow(null)).toBeNull();
    expect(
      deriveWorkflowFlow({ has_workflow: false, current_node_id: null, agents_active: 0, nodes: [] }),
    ).toBeNull();
    expect(
      deriveWorkflowFlow({ has_workflow: true, current_node_id: null, agents_active: 0, nodes: [] }),
    ).toBeNull();
    expect(
      deriveWorkflowFlow({
        has_workflow: true,
        current_node_id: 'missing',
        agents_active: 0,
        nodes: [node('n1', 'Script', 10)],
      }),
    ).toBeNull();
  });
});

describe('deriveSopFlow', () => {
  it('maps the current stage to its 1-based catalog position', () => {
    const catalog = [stage('1', 'Planning', 10), stage('2', 'Script', 20)];
    expect(deriveSopFlow(catalog, stage('2', 'Script', 20))).toEqual({
      name: 'Script',
      index: 2,
      total: 2,
    });
  });

  it('falls back to index 1 when the current stage is absent from the catalog', () => {
    const catalog = [stage('1', 'Planning', 10)];
    expect(deriveSopFlow(catalog, stage('9', 'Ghost', 90))).toEqual({
      name: 'Ghost',
      index: 1,
      total: 1,
    });
  });

  it('is null with no current stage or an empty catalog', () => {
    expect(deriveSopFlow([], null)).toBeNull();
    expect(deriveSopFlow([], stage('1', 'Planning', 10))).toBeNull();
  });
});

// ── loadProjectFlow: source switch ────────────────────────────────────────────

describe('loadProjectFlow', () => {
  it('reads the workflow node chain and never touches the SOP catalog', async () => {
    mockWorkflow.mockResolvedValue({
      has_workflow: true,
      current_node_id: 'n1',
      agents_active: 0,
      nodes: [node('n1', 'Script', 10), node('n2', 'Canvas', 20)],
    });

    const flow = await loadProjectFlow('7');

    expect(flow).toEqual({ name: 'Script', index: 1, total: 2 });
    expect(mockCurrent).not.toHaveBeenCalled();
    expect(mockCatalog).not.toHaveBeenCalled();
  });

  it('falls back to the SOP catalog for a No-workflow project', async () => {
    mockWorkflow.mockResolvedValue({
      has_workflow: false,
      current_node_id: null,
      agents_active: 0,
      nodes: [],
    });
    mockCurrent.mockResolvedValue(stage('2', 'Script', 20));
    mockCatalog.mockResolvedValue([stage('1', 'Planning', 10), stage('2', 'Script', 20)]);

    const flow = await loadProjectFlow('7');

    expect(flow).toEqual({ name: 'Script', index: 2, total: 2 });
    expect(mockCurrent).toHaveBeenCalledWith('7');
    expect(mockCatalog).toHaveBeenCalled();
  });

  it('resolves to null (ring hidden) when the workflow fetch rejects and SOP is empty', async () => {
    mockWorkflow.mockRejectedValue(new Error('down'));
    mockCurrent.mockResolvedValue(null);
    mockCatalog.mockResolvedValue([]);

    expect(await loadProjectFlow('7')).toBeNull();
  });
});
