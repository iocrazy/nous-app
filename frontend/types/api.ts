/**
 * API shapes derived from the backend's Pydantic models.
 *
 * `api.generated.d.ts` is produced from `backend/openapi.json` by
 * `npm run gen:api` and is never edited by hand. This file is the only place
 * that reaches into it: business code imports the named aliases below, so a
 * backend schema rename changes one line here instead of every call site.
 *
 * Rules (spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md):
 * - An API shape lives here as an alias of the generated schema, never as a
 *   hand-written interface in `types.ts`. Two copies of one shape may not
 *   coexist.
 * - The generated types are the real wire shape. Snowflake BIGINT ids that
 *   the backend returns as JSON numbers stay `number` — do not "fix" them to
 *   `string` here (see CLAUDE.md, "边界 mock 必须用真实 JSON 形状").
 */
import type { components } from './api.generated';

type Schemas = components['schemas'];

/**
 * The `{ success, data }` envelope that most un-typed endpoints return.
 * Replaced by the generated `Envelope[T]` once the backend declares it.
 */
export interface Envelope<T> {
  success: boolean;
  data: T;
}

export type { components, paths } from './api.generated';
export type { Schemas };

// —— Response shapes migrated off hand-written copies in types.ts (P1) ——
export type CollectionCondition = Schemas['CollectionCondition'];
export type CollectionRules = Schemas['CollectionRules'];
export type CleanupSuggestion = Schemas['CleanupSuggestion'];
export type WorkflowNodeEvents = Schemas['WorkflowNodeEvents'];
export type FormFieldDef = Schemas['FormFieldDef'];
export type AdvanceNodeRef = Schemas['AdvanceNodeRef'];
export type AdvancePreview = Schemas['AdvancePreview'];
export type StoryboardProgress = Schemas['StoryboardProgress'];
export type SuggestionAction = Schemas['SuggestionAction'];
export type ProjectSuggestionItem = Schemas['ProjectSuggestionItem'];
export type RecentItem = Schemas['RecentItem'];
export type UsagePerAgent = Schemas['UsagePerAgent'];
export type UsageAggregate = Schemas['UsageAggregate'];
export type ChatToolCall = Schemas['ChatToolCall'];
export type ChatResponse = Schemas['ChatResponse'];
export type ChatAttachmentFailure = Schemas['ChatAttachmentFailure'];
export type TopicRef = Schemas['TopicRef'];
export type MusicRef = Schemas['MusicRef'];
export type TranscriptSegment = Schemas['TranscriptSegmentSchema'];
export type ApiKey = Schemas['ApiKeyResponse'];
export type TeamMember = Schemas['TeamMemberResponse'];
export type Tag = Schemas['TagResponse'];
export type SocialAccount = Schemas['SocialAccountOut'];
export type PublishTask = Schemas['PublishTaskOut'];
export type PublishTaskAccount = Schemas['TaskAccountOut'];

// —— /api/v1/resources/* response shapes (P2) ——
// Only shapes that reach the frontend through the backend API live here. The
// hand-written `Resource` / `ResourceItem` / `Folder` in types.ts describe
// rows the browser reads straight from PostgREST (via `bigIntSafeFetch`), a
// different boundary, and stay there.
export type ResourceRow = Schemas['ResourceRow'];
export type ResourceVersion = Schemas['ResourceVersionRow'];
export type ResourceSearchResult = Schemas['ResourceSearchHit'];
export type ResourceSearchResponse = Schemas['ResourceSearchResponse'];
export type ResourceSearchCounts = Schemas['ResourceSearchCounts'];
export type GalleryChild = Schemas['GalleryChild'];
export type GalleryMembership = Schemas['GalleryMembership'];
export type ResourcePermissions = Schemas['ResourcePermissions'];
export type FolderContentCount = Schemas['FolderContentCount'];
export type ResourceBatchAiResult = Schemas['ResourceBatchAiResult'];
export type ResourceGenPrompts = Schemas['ResourceGenPrompts'];
export type ResourceDuplicateCheck = Schemas['ResourceDuplicateCheck'];
export type ResourceDuplicateCandidate = Schemas['ResourceDuplicateCandidate'];
export type CheckDuplicatesResultItem = Schemas['CheckDuplicatesResultItem'];
export type ResourceLinkExisting = Schemas['ResourceLinkExisting'];

// —— /api/v1/projects/* + /api/v1/generated-media/* response shapes (P2) ——
// Snowflake ids in this domain are JSON numbers on the wire (ProjectListItem.id,
// ProjectFileListRow.id / project_id / folder_id, …). Stringify at the edge
// (URLs, map keys, comparisons against route params) — never here.
/** A project as held in UI state — the `GET /projects` list row. The by-id,
 * create and update endpoints return narrower shapes (`ProjectDetail`,
 * `ProjectRow`); `projectsService.toProject` fills the card-enrichment
 * fields those lack so every holder sees one shape. */
export type Project = Schemas['ProjectListItem'];
export type ProjectDetail = Schemas['ProjectDetail'];
export type ProjectRow = Schemas['ProjectRow'];
export type ProjectMembersPreview = Schemas['ProjectMembersPreview'];
export type ProjectCardActivity = Schemas['ProjectFileActivity'] | Schemas['ProjectStageActivity'];
export type ProjectWorkflowBadge = Schemas['ProjectWorkflowBadge'];
/** `GET /projects/{id}/files` row. Single-file endpoints return `ProjectFileRow`. */
export type ProjectFile = Schemas['ProjectFileListRow'];
export type ProjectFileRow = Schemas['ProjectFileRow'];
export type ProjectFolder = Schemas['ProjectFolderRow'];
/** `GET/POST /projects/{id}/members`. `PUT .../members/{id}` returns `ProjectMemberRow` (no email). */
export type ProjectMember = Schemas['ProjectMemberWithEmail'];
export type ProjectMemberRow = Schemas['ProjectMemberRow'];
/** Share rows never carry the plaintext password — only `has_password`. */
export type ProjectShare = Schemas['ProjectShareRow'];
export type FileVersion = Schemas['ProjectFileVersionRow'];
/** `/projects/{id}/files/{file_id}/comments` row. */
export type ReviewComment = Schemas['ProjectFileCommentRow'];
export type ProjectStage = Schemas['ProjectStageCatalogEntry'];
export type ProjectEntities = Schemas['ProjectEntities'];
export type ProjectEntityCharacter = Schemas['ProjectCharacterEntity'];
export type ProjectEntityLocation = Schemas['ProjectLocationEntity'];
export type GeneratedMediaRow = Schemas['GeneratedMediaRow'];
export type GeneratedMediaPage = Schemas['GeneratedMediaPage'];
export type StageBoardIssueRef = Schemas['StageBoardIssueRef'];
export type StageBoardIssue = Schemas['StageBoardIssue'];
export type StageBoardFile = Schemas['StageBoardFile'];
/** Raw `GET .../nodes/{node_id}/board` payload; its `node` is a `WorkflowNodeRow`. */
export type StageBoard = Schemas['StageBoard'];
export type NodeOut = Schemas['NodeOut'];
export type WorkflowNodeRow = Schemas['WorkflowNodeRow'];
export type WorkflowNodeStatus = NodeOut['status'];
/** Raw `GET /projects/{id}/workflow` payload. */
export type ProjectWorkflowOut = Schemas['ProjectWorkflowOut'];
/** A live workflow node after `workflowService.normalizeInstanceNode`: the
 * wire `NodeOut` with every defaulted field made present, so consumers never
 * see `undefined` on a legacy row. Wire → this only through that function. */
export type ProjectStageNode = Omit<NodeOut, 'metadata'> &
  Required<Pick<NodeOut, 'members' | 'events' | 'form_schema' | 'form_data' | 'depends_on' | 'surface'>> & {
    /** Today the only key written is `run_prepared_at` (mig 389). */
    metadata: { run_prepared_at?: string };
  };
/** `fetchProjectWorkflow` result — nodes normalized, always an array. */
export type ProjectWorkflow = Omit<ProjectWorkflowOut, 'nodes'> & { nodes: ProjectStageNode[] };
/** `fetchStageBoard` result — the node normalized like every other node payload. */
export type StageBoardData = Omit<StageBoard, 'node'> & { node: ProjectStageNode };
export type ResourceLyricsUpload = Schemas['ResourceLyricsUpload'];
