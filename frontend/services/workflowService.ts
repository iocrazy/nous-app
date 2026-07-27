/**
 * Project Workflow API service (M1 PR-C).
 *
 * Team workflow templates + the node bank live under `/api/v1/workflows`;
 * per-project instance nodes + the advance chain live under
 * `/api/v1/projects/{id}/...`. Ids ride as strings end to end (bigIntSafeFetch).
 *
 * Envelope discipline is per-endpoint (mirrors projectsService.ts):
 *   - Template CRUD, stage-library, PATCH node, POST advance → `{ data }` wrapped.
 *   - GET /workflow and GET /advance-preview declare a FastAPI `response_model`,
 *     so they return the model DIRECTLY — do NOT unwrap `.data` there.
 */

import {
  AdvancePreview,
  ProjectNodeCreate,
  ProjectNodePatch,
  ProjectStageNode,
  ProjectWorkflow,
  StageBoardData,
  StageLibraryItem,
  WorkflowCompletionPolicy,
  WorkflowNodeEvents,
  WorkflowTemplate,
  WorkflowTemplateNode,
  WorkflowTemplateNodeInput,
} from '../types';
import { apiClient } from './apiClient';

interface Envelope<T> {
  data?: T;
}

/** Backend defaults (schemas/workflow.py) — applied when an older payload
 * (pre mig 386) is missing these fields, so neither the template editor nor
 * any instance-node consumer (e.g. the E3 suggest-agent-run chip reading
 * `node.events.notify_on_arrival`) ever crashes on a legacy template/instance.
 * Exported so callers needing a fresh default object (e.g. a new draft node)
 * don't duplicate the literal. */
export const DEFAULT_COMPLETION_POLICY: WorkflowCompletionPolicy = 'owner';
export const DEFAULT_EVENTS: WorkflowNodeEvents = {
  notify_on_arrival: true,
  notify_on_complete: false,
  suggest_agent_run: false,
};

const normalizeEvents = (events: Partial<WorkflowNodeEvents> | null | undefined): WorkflowNodeEvents => ({
  notify_on_arrival: events?.notify_on_arrival ?? DEFAULT_EVENTS.notify_on_arrival,
  notify_on_complete: events?.notify_on_complete ?? DEFAULT_EVENTS.notify_on_complete,
  suggest_agent_run: events?.suggest_agent_run ?? DEFAULT_EVENTS.suggest_agent_run,
});

/** Fill in `completion_policy`/`events` on a template node fetched from a
 * payload that may predate mig 386 (both fields optional server-side too). */
const normalizeTemplateNode = (
  node: Partial<WorkflowTemplateNode> & Omit<WorkflowTemplateNode, 'completion_policy' | 'events'>,
): WorkflowTemplateNode => ({
  ...node,
  completion_policy: node.completion_policy ?? DEFAULT_COMPLETION_POLICY,
  events: normalizeEvents(node.events),
});

const normalizeTemplate = (template: WorkflowTemplate): WorkflowTemplate => ({
  ...template,
  nodes: template.nodes?.map(normalizeTemplateNode),
});

/** Same normalization for a live instance node (`project_stage_nodes`) —
 * GET /projects/{id}/workflow, POST .../nodes and PATCH .../nodes/{id} all
 * round-trip through this so a pre-mig-386 row never hands `undefined` to a
 * consumer reading e.g. `node.events.notify_on_arrival`. */
const normalizeInstanceNode = (
  node: Partial<ProjectStageNode> & Omit<ProjectStageNode, 'completion_policy' | 'events'>,
): ProjectStageNode => ({
  ...node,
  completion_policy: node.completion_policy ?? DEFAULT_COMPLETION_POLICY,
  events: normalizeEvents(node.events),
});

// ============================================
// Team workflow templates
// ============================================

/** List a team's templates (seeds the two built-ins on first access). */
export const fetchTemplates = async (
  teamId: string,
): Promise<WorkflowTemplate[]> => {
  const response = await apiClient.get<Envelope<WorkflowTemplate[]>>(
    '/api/v1/workflows',
    { query: { team_id: teamId } },
  );
  // A missing `data` key means the wrong handler answered (this exact path
  // was once shadowed by the DBOS runs list) — surface it, never mask it
  // as an empty template list.
  if (!Array.isArray(response.data)) {
    throw new Error('Unexpected response shape from fetchTemplates');
  }
  return response.data;
};

/** One template with its ordered nodes + members. */
export const fetchTemplate = async (
  templateId: string,
): Promise<WorkflowTemplate> => {
  const response = await apiClient.get<Envelope<WorkflowTemplate>>(
    `/api/v1/workflows/${templateId}`,
  );
  if (!response.data) throw new Error('Empty response from fetchTemplate');
  return normalizeTemplate(response.data);
};

/** Create an empty named template (nodes are set via updateTemplate). */
export const createTemplate = async (
  teamId: string,
  name: string,
): Promise<WorkflowTemplate> => {
  const response = await apiClient.post<Envelope<WorkflowTemplate>>(
    '/api/v1/workflows',
    { name },
    { query: { team_id: teamId } },
  );
  if (!response.data) throw new Error('Empty response from createTemplate');
  return normalizeTemplate(response.data);
};

/**
 * Patch a template. `nodes` (when present) FULLY replaces the node list;
 * `is_default: true` promotes this template and demotes its siblings.
 */
export const updateTemplate = async (
  templateId: string,
  data: {
    name?: string;
    is_default?: boolean;
    nodes?: WorkflowTemplateNodeInput[];
  },
): Promise<WorkflowTemplate> => {
  const response = await apiClient.patch<Envelope<WorkflowTemplate>>(
    `/api/v1/workflows/${templateId}`,
    data,
  );
  if (!response.data) throw new Error('Empty response from updateTemplate');
  return normalizeTemplate(response.data);
};

export const deleteTemplate = async (templateId: string): Promise<void> => {
  await apiClient.delete(`/api/v1/workflows/${templateId}`);
};

/** The read-only 11-node workflow node bank. */
export const fetchStageLibrary = async (): Promise<StageLibraryItem[]> => {
  const response = await apiClient.get<Envelope<StageLibraryItem[]>>(
    '/api/v1/workflows/stage-library',
  );
  return response.data ?? [];
};

// ============================================
// Per-project workflow instance
// ============================================

/** The project's instance nodes + cursor + running-agent count. */
export const fetchProjectWorkflow = async (
  projectId: string,
): Promise<ProjectWorkflow> => {
  // NOTE: declares a FastAPI response_model → returns the model directly (no
  // `{data}` envelope). Do not add a `.data` unwrap here.
  const workflow = await apiClient.get<ProjectWorkflow>(
    `/api/v1/projects/${projectId}/workflow`,
  );
  return { ...workflow, nodes: (workflow.nodes ?? []).map(normalizeInstanceNode) };
};

/**
 * Add a node to a live instance (M2-W3-1) — from the node bank
 * (`source_stage_id`) or blank (`name`). Returns the created node.
 */
export const addProjectNode = async (
  projectId: string,
  body: ProjectNodeCreate,
): Promise<ProjectStageNode> => {
  const response = await apiClient.post<Envelope<ProjectStageNode>>(
    `/api/v1/projects/${projectId}/workflow/nodes`,
    body,
  );
  if (!response.data) throw new Error('Empty response from addProjectNode');
  return normalizeInstanceNode(response.data);
};

/**
 * Remove a node from a live instance (M2-W3-1). The server 409s (ApiError,
 * status 409) with a machine reason when the node is not removable (started,
 * carries a mirror issue, or is the active group) — callers should surface it.
 */
export const deleteProjectNode = async (
  projectId: string,
  nodeId: string,
): Promise<void> => {
  await apiClient.delete(
    `/api/v1/projects/${projectId}/workflow/nodes/${nodeId}`,
  );
};

/** In-place tweak of a live node (owner / members / schedule / skipped). */
export const updateProjectNode = async (
  projectId: string,
  nodeId: string,
  patch: ProjectNodePatch,
): Promise<ProjectStageNode> => {
  const response = await apiClient.patch<Envelope<ProjectStageNode>>(
    `/api/v1/projects/${projectId}/workflow/nodes/${nodeId}`,
    patch,
  );
  if (!response.data) throw new Error('Empty response from updateProjectNode');
  return normalizeInstanceNode(response.data);
};

/** Pure-read advance ruling (same predicate as executeAdvance). */
export const fetchAdvancePreview = async (
  projectId: string,
  direction: 'forward' | 'back' = 'forward',
): Promise<AdvancePreview> => {
  // response_model endpoint → returned directly, no envelope.
  return apiClient.get<AdvancePreview>(
    `/api/v1/projects/${projectId}/advance-preview`,
    { query: { direction } },
  );
};

/**
 * Advance / retreat the workflow cursor. The server recomputes the predicate
 * and 409s (ApiError, status 409) with the blocked reason if it no longer
 * clears — callers should catch that and re-open the preview.
 */
export const executeAdvance = async (
  projectId: string,
  direction: 'forward' | 'back' = 'forward',
): Promise<AdvancePreview> => {
  const response = await apiClient.post<Envelope<AdvancePreview>>(
    `/api/v1/projects/${projectId}/advance`,
    undefined,
    { query: { direction } },
  );
  if (!response.data) throw new Error('Empty response from executeAdvance');
  return response.data;
};

/**
 * Stage Board aggregate (M2 PR-F F1/F2) — one node's full row + its mirror
 * issue (with sub-issues) + the files filed into its deliverable folder. The
 * instance-node segment rides through the same `normalizeInstanceNode` fixup
 * as every other node payload so a pre-mig-386 row never hands `undefined` to
 * a consumer reading `node.events.notify_on_arrival`.
 */
export const fetchStageBoard = async (
  projectId: string,
  nodeId: string,
): Promise<StageBoardData> => {
  const response = await apiClient.get<Envelope<StageBoardData>>(
    `/api/v1/projects/${projectId}/workflow/nodes/${nodeId}/board`,
  );
  if (!response.data) throw new Error('Empty response from fetchStageBoard');
  return { ...response.data, node: normalizeInstanceNode(response.data.node) };
};
