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

import { ProjectNodeCreate, ProjectNodePatch, WorkflowCompletionPolicy, WorkflowTemplateNodeInput } from '../types';
import type { AdvancePreview, FormFieldDef, NodeOut, ProjectStageNode, ProjectWorkflow, ProjectWorkflowOut, StageBoard, StageBoardData, StageLibraryItem, WorkflowNodeEvents, WorkflowNodeRow, WorkflowTemplate, WorkflowTemplateDetail, WorkflowTemplateNode, WorkflowTemplateNodeRow, WorkflowTemplateSummary } from '../types/api';
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
/** Mirrors backend `MAX_FORM_FIELDS` (schemas/workflow.py) — the template
 * editor's Form tab disables "+ Add field" at this count so the 422 the
 * server would throw at 21 is never reachable through the UI. */
export const MAX_FORM_FIELDS = 20;
export const DEFAULT_EVENTS: WorkflowNodeEvents = {
  notify_on_arrival: true,
  notify_on_complete: false,
  suggest_agent_run: false,
  // mig 389 (M3 PR-H): arrival hook flag + the (unimplemented, structure-only)
  // completion hook — see WorkflowNodeEvents in types.ts.
  prepare_agent_run: false,
  on_complete_workflow: null,
  // mig 395 (M4 Autopilot, task O1/O2): default OFF — merging this PR must be
  // a zero-behavior-change no-op until a template explicitly opts a node in
  // (spec §5 "默认全关").
  auto_start: false,
};

const normalizeEvents = (events: Partial<WorkflowNodeEvents> | null | undefined): WorkflowNodeEvents => ({
  notify_on_arrival: events?.notify_on_arrival ?? DEFAULT_EVENTS.notify_on_arrival,
  notify_on_complete: events?.notify_on_complete ?? DEFAULT_EVENTS.notify_on_complete,
  suggest_agent_run: events?.suggest_agent_run ?? DEFAULT_EVENTS.suggest_agent_run,
  prepare_agent_run: events?.prepare_agent_run ?? DEFAULT_EVENTS.prepare_agent_run,
  on_complete_workflow: events?.on_complete_workflow ?? DEFAULT_EVENTS.on_complete_workflow,
  auto_start: events?.auto_start ?? DEFAULT_EVENTS.auto_start,
});

/** Narrow a template node's stored JSONB (`events` / `form_schema`) and
 * default any key a legacy row predates (mig 386/389/395 events keys; the
 * columns are NOT NULL, so the lists themselves are always present). */
const normalizeTemplateNode = (node: WorkflowTemplateNodeRow): WorkflowTemplateNode => ({
  ...node,
  // The server only ever writes a `WorkflowNodeEvents` into this JSONB, and
  // normalizeEvents defaults any key that's missing.
  events: normalizeEvents(node.events as Partial<WorkflowNodeEvents>),
  // Copied verbatim from validated `FormFieldDef[]` on write (mig 390).
  form_schema: node.form_schema as FormFieldDef[],
});

const normalizeTemplate = (template: WorkflowTemplateDetail): WorkflowTemplate => ({
  ...template,
  nodes: template.nodes.map(normalizeTemplateNode),
});

const COMPLETION_POLICIES: readonly WorkflowCompletionPolicy[] = ['owner', 'any_editor'];
const NODE_SURFACES = ['script', 'storyboard', 'renders'] as const;
type NodeSurface = (typeof NODE_SURFACES)[number];

const isCompletionPolicy = (v: unknown): v is WorkflowCompletionPolicy =>
  typeof v === 'string' && (COMPLETION_POLICIES as readonly string[]).includes(v);
const isNodeSurface = (v: unknown): v is NodeSurface =>
  typeof v === 'string' && (NODE_SURFACES as readonly string[]).includes(v);

/** Same normalization for a live instance node (`project_stage_nodes`) —
 * GET /projects/{id}/workflow (`NodeOut`) and the node write endpoints + the
 * Stage Board (`WorkflowNodeRow`, the bare DB row) all round-trip through this
 * so a pre-mig-386 row never hands `undefined` to a consumer reading e.g.
 * `node.events.notify_on_arrival`. The two wire shapes differ only in how
 * loosely the JSONB / enum columns are typed; this is the one place that
 * narrows them. */
const normalizeInstanceNode = (node: NodeOut | WorkflowNodeRow): ProjectStageNode => ({
  ...node,
  completion_policy: isCompletionPolicy(node.completion_policy)
    ? node.completion_policy
    : DEFAULT_COMPLETION_POLICY,
  // `WorkflowNodeRow.events` is the raw JSONB dict; the server only ever
  // writes a `WorkflowNodeEvents` into it, and normalizeEvents defaults any
  // key that's missing, so reading it as a partial of that shape is safe.
  events: normalizeEvents(node.events as Partial<WorkflowNodeEvents> | undefined),
  // mig 389 (M3 PR-H3): tolerate a row that predates the metadata column (or
  // one the stage hook never touched) — the Run now chip reads
  // `node.metadata?.run_prepared_at` and must never see `undefined` blow up
  // into a crash, just an absent key.
  metadata:
    typeof node.metadata?.run_prepared_at === 'string'
      ? { run_prepared_at: node.metadata.run_prepared_at }
      : {},
  // mig 390 (M3 PR-I §2, task I4): same tolerance for a pre-mig-390 row —
  // StageNodeForm/CurrentNodeCard's form-count row iterate these directly
  // and must never see `undefined`. The row's column is `unknown[]`; it is
  // copied verbatim from the template's validated `FormFieldDef[]` (I2).
  form_schema: (node.form_schema ?? []) as FormFieldDef[],
  form_data: node.form_data ?? {},
  // mig 391 (M3 PR-J): dependency edges — real, stable instance node ids.
  // Tolerate a pre-mig-391 row the same way as the fields above.
  depends_on: node.depends_on ?? [],
  // mig 402 (B1): the frozen creative surface. Missing / unknown (legacy
  // instance rows pre-mig-402) degrades to `null` = deliverable-only node
  // (spec §5③, the most conservative fallback). B5 reads this for episode
  // view tabs + the deliverable-node dashed border.
  surface: isNodeSurface(node.surface) ? node.surface : null,
  // mig 395 (M4 Autopilot, task O1/O2): pre-work notes — CurrentNodeCard's
  // and WorkspaceStageBoard's brief textareas seed straight off this and must
  // never see `undefined` (the defaulted-seed idiom, StageNodeForm's
  // `lastSaved` fix) on a pre-mig-395 row.
  brief: node.brief ?? '',
  members: node.members ?? [],
  // `WorkflowNodeRow` doesn't carry the filed-file count (only NodeOut does).
  deliverable_file_count: 'deliverable_file_count' in node ? node.deliverable_file_count : 0,
});

// ============================================
// Team workflow templates
// ============================================

/** List a team's templates (seeds the two built-ins on first access).
 * `teamId` omitted/empty resolves server-side to the CALLER's own personal
 * team — the personal-workspace create flow (no real team selected) uses
 * this to load its own seeded templates. */
export const fetchTemplates = async (
  teamId?: string,
): Promise<WorkflowTemplate[]> => {
  const response = await apiClient.get<Envelope<WorkflowTemplateSummary[]>>(
    '/api/v1/workflows',
    { query: { team_id: teamId || undefined } },
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
  const response = await apiClient.get<Envelope<WorkflowTemplateDetail>>(
    `/api/v1/workflows/${templateId}`,
  );
  if (!response.data) throw new Error('Empty response from fetchTemplate');
  return normalizeTemplate(response.data);
};

/** Create an empty named template (nodes are set via updateTemplate). The
 * response is the list row: no `nodes` key. */
export const createTemplate = async (
  teamId: string,
  name: string,
): Promise<WorkflowTemplate> => {
  const response = await apiClient.post<Envelope<WorkflowTemplateSummary>>(
    '/api/v1/workflows',
    { name },
    { query: { team_id: teamId } },
  );
  if (!response.data) throw new Error('Empty response from createTemplate');
  return response.data;
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
  const response = await apiClient.patch<Envelope<WorkflowTemplateDetail>>(
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

/** The project's instance nodes + cursor + running-agent count.
 * `episodeId` (B2 #1712) narrows the read to one episode's frozen row set.
 * REQUIRED (B6 PR-2 task 10) — the server 422s on a bare project-level read
 * now that the legacy no-episode path is retired; callers must gate on a
 * resolved episode id before calling this (see useProjectWorkflow's null
 * check). */
export const fetchProjectWorkflow = async (
  projectId: string,
  episodeId: string,
): Promise<ProjectWorkflow> => {
  // NOTE: declares a FastAPI response_model → returns the model directly (no
  // `{data}` envelope). Do not add a `.data` unwrap here.
  const workflow = await apiClient.get<ProjectWorkflowOut>(
    `/api/v1/projects/${projectId}/workflow`,
    { query: { episode_id: episodeId } },
  );
  return { ...workflow, nodes: (workflow.nodes ?? []).map(normalizeInstanceNode) };
};

/**
 * Attach a workflow template to an EXISTING project that has none yet (M1.x
 * opt-in migration path — the answer to "migrate the old SOP projects"
 * without a lossy bulk script: the user opts a project in and picks the
 * template themselves, one at a time). The server 409s (ApiError) if the
 * project already has a workflow, and 404s if the template doesn't belong
 * to the project's own scope (its team, or the owner's personal team for a
 * personal project). Returns the freshly-instantiated node list.
 */
export const attachProjectWorkflow = async (
  projectId: string,
  body: { template_id: string; method?: 'live' | 'ai' | 'hybrid' | null },
): Promise<ProjectStageNode[]> => {
  const response = await apiClient.post<Envelope<WorkflowNodeRow[]>>(
    `/api/v1/projects/${projectId}/workflow`,
    body,
  );
  return (response.data ?? []).map(normalizeInstanceNode);
};

/**
 * Add a node to a live instance (M2-W3-1) — from the node bank
 * (`source_stage_id`) or blank (`name`). Returns the created node.
 */
export const addProjectNode = async (
  projectId: string,
  body: ProjectNodeCreate,
): Promise<ProjectStageNode> => {
  const response = await apiClient.post<Envelope<WorkflowNodeRow>>(
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
  const response = await apiClient.patch<Envelope<WorkflowNodeRow>>(
    `/api/v1/projects/${projectId}/workflow/nodes/${nodeId}`,
    patch,
  );
  if (!response.data) throw new Error('Empty response from updateProjectNode');
  return normalizeInstanceNode(response.data);
};

/**
 * Manual "Start early" (M4 Autopilot, task O2/O3) — begin work on a future
 * (not-yet-current) node ahead of the normal cursor flow, once its
 * dependencies are actually satisfied. The hand-operated twin of the
 * autopilot engine's own auto-start step; an agent owner is NEVER
 * auto-dispatched from this path (server always routes it to the M3 confirm
 * gate instead). The server 422s (ApiError, status 422) with a machine
 * `code` — `NODE_NOT_PENDING` / `NODE_CANCELLED` / the shared `DEPS_PENDING`
 * (carrying `waiting_on` in `details`, same shape as `AdvancePreview`) — when
 * it no longer clears; callers should catch that and surface mapped copy.
 *
 * `episodeId` REQUIRED (B6 PR-2 task 10) — same rationale as
 * `fetchProjectWorkflow`; callers must gate on a resolved episode id first.
 */
export const startEarlyNode = async (
  projectId: string,
  nodeId: string,
  episodeId: string,
): Promise<ProjectStageNode> => {
  const response = await apiClient.post<Envelope<WorkflowNodeRow>>(
    `/api/v1/projects/${projectId}/workflow/nodes/${nodeId}/start-early`,
    undefined,
    { query: { episode_id: episodeId } },
  );
  if (!response.data) throw new Error('Empty response from startEarlyNode');
  return normalizeInstanceNode(response.data);
};

/** Pure-read advance ruling (same predicate as executeAdvance). `episodeId`
 * REQUIRED (B6 PR-2 task 10) — same rationale as `fetchProjectWorkflow`. */
export const fetchAdvancePreview = async (
  projectId: string,
  direction: 'forward' | 'back',
  episodeId: string,
): Promise<AdvancePreview> => {
  // response_model endpoint → returned directly, no envelope.
  return apiClient.get<AdvancePreview>(
    `/api/v1/projects/${projectId}/advance-preview`,
    { query: { direction, episode_id: episodeId } },
  );
};

/**
 * Advance / retreat the workflow cursor. The server recomputes the predicate
 * and 409s (ApiError, status 409) with the blocked reason if it no longer
 * clears — callers should catch that and re-open the preview.
 *
 * `episodeId` REQUIRED (B6 PR-2 task 10) — same rationale as
 * `fetchProjectWorkflow`.
 */
export const executeAdvance = async (
  projectId: string,
  direction: 'forward' | 'back',
  episodeId: string,
): Promise<AdvancePreview> => {
  const response = await apiClient.post<Envelope<AdvancePreview>>(
    `/api/v1/projects/${projectId}/advance`,
    undefined,
    { query: { direction, episode_id: episodeId } },
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
  const response = await apiClient.get<Envelope<StageBoard>>(
    `/api/v1/projects/${projectId}/workflow/nodes/${nodeId}/board`,
  );
  if (!response.data) throw new Error('Empty response from fetchStageBoard');
  return { ...response.data, node: normalizeInstanceNode(response.data.node) };
};
