/**
 * loadProjectFlow — W2-2 single-point flow source for the issue context bar.
 * A workflow project reads its node chain; a No-workflow project resolves to
 * null (the legacy SOP catalog fallback was retired in G3).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/workflowService', () => ({
  fetchProjectWorkflow: vi.fn(),
}));

import { deriveWorkflowFlow, loadProjectFlow } from './issueFlow';
import { fetchProjectWorkflow } from '../../services/workflowService';

const mockWorkflow = fetchProjectWorkflow as unknown as ReturnType<typeof vi.fn>;

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

// ── loadProjectFlow: source switch ────────────────────────────────────────────

describe('loadProjectFlow', () => {
  it('reads the workflow node chain', async () => {
    mockWorkflow.mockResolvedValue({
      has_workflow: true,
      current_node_id: 'n1',
      agents_active: 0,
      nodes: [node('n1', 'Script', 10), node('n2', 'Canvas', 20)],
    });

    const flow = await loadProjectFlow('7');

    expect(flow).toEqual({ name: 'Script', index: 1, total: 2 });
  });

  it('resolves to null (ring hidden) for a No-workflow project', async () => {
    mockWorkflow.mockResolvedValue({
      has_workflow: false,
      current_node_id: null,
      agents_active: 0,
      nodes: [],
    });

    expect(await loadProjectFlow('7')).toBeNull();
  });

  it('resolves to null (ring hidden) when the workflow fetch rejects', async () => {
    mockWorkflow.mockRejectedValue(new Error('down'));

    expect(await loadProjectFlow('7')).toBeNull();
  });
});
