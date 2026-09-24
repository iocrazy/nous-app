/**
 * Wire-shape fixtures for the /api/v1/projects domain.
 *
 * Every field the generated schema marks required is present, and Snowflake
 * ids are JSON numbers exactly as the backend sends them (CLAUDE.md,
 * "边界 mock 必须用真实 JSON 形状"). Tests override only what they assert on.
 */
import type { NodeOut, Project, ProjectStageNode } from '../../types/api';

/** A `GET /projects` list row. */
export function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 1,
    name: 'Project',
    description: null,
    owner_id: 'u1',
    team_id: null,
    project_type: 'internal',
    project_group: null,
    announcement: null,
    is_starred: false,
    color_label: null,
    archived_at: null,
    file_count: 0,
    autopilot_enabled: true,
    visibility: 'private',
    topic_id: null,
    current_canvas_id: null,
    current_node_id: null,
    current_stage: null,
    workflow_id: null,
    workflow_method: null,
    workflow_template_id: null,
    latest_activity: null,
    members_preview: null,
    workflow_badge: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

/** A live workflow node as `normalizeInstanceNode` hands it to consumers. */
export function makeStageNode(overrides: Partial<ProjectStageNode> = {}): ProjectStageNode {
  return {
    id: 'n1',
    project_id: '1',
    source_template_node_id: null,
    legacy_stage_id: null,
    episode_id: null,
    name: 'Node',
    sort_order: 0,
    parallel_group: null,
    status: 'pending',
    owner_user_id: null,
    owner_agent_id: null,
    planned_start: null,
    planned_due: null,
    review_required: false,
    deliverable_required: false,
    deliverable_label: null,
    deliverable_file_count: 0,
    folder_id: null,
    skipped: false,
    surface: null,
    brief: '',
    members: [],
    completion_policy: 'owner',
    events: {
      notify_on_arrival: true,
      notify_on_complete: false,
      suggest_agent_run: false,
      prepare_agent_run: false,
      on_complete_workflow: null,
      auto_start: false,
    },
    metadata: {},
    form_schema: [],
    form_data: {},
    depends_on: [],
    ...overrides,
  };
}

/** A raw `NodeOut` as the workflow endpoint sends it (before normalization). */
export function makeNodeOut(overrides: Partial<NodeOut> = {}): NodeOut {
  const { metadata, ...node } = makeStageNode();
  return { ...node, metadata, ...overrides };
}
