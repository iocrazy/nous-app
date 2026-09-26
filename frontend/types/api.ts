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

// --- P3: generated inbox (/api/v1/generated) ---
// Snowflake ids are strings on this surface (the repository `_normalize`s
// them); `created_at` is Pydantic's rendering (`…Z`), not `isoformat()`.
export type GeneratedItem = Schemas['GeneratedItem'];
export type GeneratedSource = Schemas['GeneratedSource'];
export type GeneratedPage = Schemas['GeneratedPage'];
export type GeneratedCounts = Schemas['GeneratedCounts'];
export type GeneratedReviewState = GeneratedItem['review_state'];
export type GeneratedBatchAction = Schemas['BatchRequest']['action'];
export type GeneratedNewAssetSpec = Schemas['NewAssetSpec'];
export type GeneratedBatchFailure = Schemas['GeneratedBatchFailure'];
export type GeneratedBatchResult = Schemas['GeneratedBatchResult'];
export type GeneratedCleanupResult = Schemas['GeneratedCleanupResponse'];
export type GeneratedSaveAsAssetResult = Schemas['GeneratedSaveAsAssetResult'];
/** `POST /resources/{id}/save-as-asset`: the generation route's keys + `generated_id`. */
export type ResourceSaveAsAssetResult = Schemas['ResourceSaveAsAssetResult'];
// --- end generated inbox ---

// --- P3: script projects (/api/v1/scripts/projects) ---
/** One `script_projects` row. Ids are JSON numbers on this surface (plain
 * `fetch`, no bigIntSafeFetch); `String()` them at URL / key / compare sites. */
export type ScriptProject = Schemas['ScriptProjectRow'];
/** `GET /scripts/projects?project_id=` payload: full rows, not summaries. */
export type ScriptProjectPage = Schemas['ScriptProjectPage'];
/** Raw `GET /scripts/projects/{id}` payload: `{ project, chapters }`. */
export type ScriptProjectDetail = Schemas['ScriptProjectFull'];
export type ScriptProjectUpdate = Schemas['ScriptProjectUpdate'];
export type ScriptChapter = Schemas['ScriptChapterRow'];
export type ScriptAsset = Schemas['ScriptAssetRow'];
// --- end script projects ---

// --- P3: canvases, episodes, project assets ---
/** A full canvas document as the canvas routes send it (ids are strings). */
export type CanvasRow = Schemas['CanvasRow'];
/** `GET /projects/{id}/canvases` row: summary columns + `node_count`, no graph. */
export type CanvasSummary = Schemas['CanvasSummary'];
export type TeamCanvasSummary = Schemas['TeamCanvasSummary'];
export type TeamCanvasProject = Schemas['TeamCanvasProject'];
export type ProjectTrashedCanvas = Schemas['ProjectTrashedCanvas'];
export type TeamTrashedCanvas = Schemas['TeamTrashedCanvas'];
export type CanvasAssetRef = Schemas['CanvasAssetRef'];
/** A live canvas that references a resource (`GET /resources/{id}/canvas-refs`). */
export type ResourceCanvasRef = Schemas['ResourceCanvasRef'];
export type CanvasGenerationCapability = Schemas['CanvasGenerationCapability'];
/** `episodes` row — bigint ids are JSON numbers on this surface. */
export type EpisodeRow = Schemas['EpisodeRow'];
export type EpisodeListRow = Schemas['EpisodeListRow'];
export type EpisodeProgress = Schemas['EpisodeProgressRow'];
export type EpisodeStatus = EpisodeProgress['status'];
export type EpisodeWorkflowRollup = Schemas['EpisodeWorkflowRollup'];
export type EpisodeSurfaceState = Schemas['EpisodeSurfaceState'];
// --- end canvases ---

// —— points (/api/v1/points/*) (P4) ——
/** `GET /points/balance` payload. `team_id` is a string here (the resolved id). */
export type PointsBalance = Schemas['PointsBalance'];
/** One ledger row. `id` / `team_id` are JSON numbers (bigint); `created_at`
 * is an ISO string with `+00:00`; `duration_seconds` is a numeric string. */
export type PointsTransaction = Schemas['PointsTransactionRow'];
export type PointsTransactionType = PointsTransaction['type'];
/** `GET /points/transactions`: `{ success, count, transactions }`, no `data`. */
export type PointsTransactionsResponse = Schemas['PointsTransactionsResponse'];
export type PointsPricing = Schemas['PointsPricingRow'];
/** `GET /points/pricing`: `{ success, pricing }`, no `data`. */
export type PointsPricingResponse = Schemas['PointsPricingResponse'];
/** All-time totals only — no per-month or per-member breakdown exists. */
export type PointsUsageStats = Schemas['PointsUsageStats'];
/** 200 path only (a denial is a 402). `current_balance` is null for free actions. */
export type PointsQuotaCheck = Schemas['PointsQuotaCheck'];
// —— end points ——

// —— canvas tasks (P4) ——
/** `GET /canvases/generations/{task_id}` payload: one `task_tracking` row.
 * `phase` is null until the engine picks the task up; `metadata` is the
 * workflow's open jsonb (narrowed by `GenerationTask` in canvasGenerationService). */
export type CanvasGenerationTask = Schemas['CanvasGenerationTask'];
/** `POST /canvases/{id}/generations`: `{ success, task_ids, flow_id }`, no `data`. */
export type CanvasGenerationDispatch = Schemas['CanvasGenerationDispatch'];
export type CanvasTimelineDispatch = Schemas['CanvasTimelineDispatch'];
/** `POST /canvases/runs/prompts` payload; failures are in-band (`ok: false`). */
export type CanvasPromptRunResponse = Schemas['CanvasPromptRunResponse'];
/** One derive product; `id` is a generated_media Snowflake as a string. */
export type CanvasDerivedImage = Schemas['CanvasDerivedImage'];
export type CanvasDeriveResult = Schemas['CanvasDeriveResult'];
// —— end canvas tasks ——

// —— task-manager (/api/v1/task-manager/*) (P4) ——
/** A `task_tracking` row as the REST list returns it: every column, PK is
 * `dbos_workflow_id`, timestamps are ISO strings with `+00:00`, `issue_id` is
 * a JSON number. Realtime rows (PostgREST) are a different boundary and stay
 * untyped. The UI model built from either is `UnifiedTask`. */
export type TaskTrackingRow = Schemas['TaskTrackingRow'];
/** `GET /task-manager/tasks`: `{ success, data, total, page, page_size }`. */
export type TaskListPage = Schemas['TaskListPage'];
/** `GET /task-manager/tasks/ids`: `{ success, ids, total, capped }`, no `data`. */
export type TaskIdList = Schemas['TaskIdList'];
export type TaskActiveCounts = Schemas['TaskActiveCounts'];
export type TaskActiveCountsResponse = Schemas['Envelope_TaskActiveCounts_'];
export type TaskStats = Schemas['TaskStats'];
export type TaskClearCompletedResult = Schemas['TaskClearCompletedResult'];
export type TaskHealthOverrideResult = Schemas['TaskHealthOverrideResult'];
export type TaskExtendResult = Schemas['TaskExtendResult'];
/** `downloaded` only on the live (Redis) branch; `speed` is a string there
 * and the raw bytes/s number on the DB fallback. */
export type TaskProgress = Schemas['TaskProgress'];
// —— end task-manager ——

// —— AI Library (P4) ——
/** `GET /ai-library/agents/{slug}/status` — the header chip. */
export type AgentStatus = Schemas['AgentStatusOut'];
/** `GET /ai-library/agents/{slug}/dashboard` — 14 days, caller-scoped.
 * Run ids are numeric strings; timestamps are ISO strings with `+00:00`. */
export type AgentDashboard = Schemas['AgentDashboard'];
/** `GET /ai-library/agents/{slug}/usage` — the "Used by" card. */
export type AgentUsage = Schemas['AgentUsageOut'];
/** One row of `GET /ai-library/runs/live` (the Workforce "Running now" strip). */
export type LiveAgentRun = Schemas['LiveRunItem'];
/** Every event type the backend CHECK allows (mig 285 / 436 / 443 / 453).
 * The union is open-ended on purpose: `foldEvents` ignores what it does not
 * know, so a new backend type never breaks an old client. */
export type AgentRunEventType =
  | 'user'
  | 'assistant'
  | 'tool_call'
  | 'error'
  | 'system'
  | 'capability_denied'
  | 'llm_retry'
  | 'todo_write'
  | 'compaction_start'
  | 'compaction_summary'
  | 'compaction_end'
  | 'turn_end'
  | 'step_start'
  | 'step_end'
  | 'inbox_claimed'
  | 'deliverable'
  | 'budget_check'
  | (string & {});
/** One transcript event (`GET /ai-library/runs/{id}/events`). `turn` / `step`
 * are always sent; null on rows written before mig 453. */
export type AgentRunEvent = Omit<Schemas['RunTranscriptEvent'], 'event_type'> & {
  event_type: AgentRunEventType;
};
export type RunTranscriptPage = Omit<Schemas['RunTranscriptPage'], 'items'> & {
  items: AgentRunEvent[];
};
/** `GET /ai-library/runs/{id}/view-at?seq=` — the fold registry's open views. */
export type RunViewAt = Schemas['RunViewAt'];
/** `POST /ai-library/runs/{id}/fork` (201). `run_id` is always null; `issue_id`
 * and `forked_from.run_id` are JSON numbers on this surface. */
export type RunForkResult = Schemas['RunForkResult'];
export type RunForkItem = Schemas['RunForkItem'];
export type AgentVersionListItem = Schemas['AgentVersionListItem'];
export type SkillVersionListItem = Schemas['SkillVersionListItem'];
export type SkillFileVersionListItem = Schemas['SkillFileVersionListItem'];
/** A version-history row of any of the three kinds (list view: no bodies). */
export type AILibraryVersionItem =
  | AgentVersionListItem
  | SkillVersionListItem
  | SkillFileVersionListItem;
export type AgentVersionList = Schemas['AgentVersionList'];
export type SkillVersionList = Schemas['SkillVersionList'];
export type AgentVersionDetail = Schemas['AgentVersionDetail'];
export type SkillVersionDetail = Schemas['SkillVersionDetail'];
export type VersionRollbackResult = Schemas['VersionRollbackResult'];
/** The bearer token never comes back; only `has_bearer_token`. */
export type AILibraryMCPServer = Schemas['McpServerOut'];
export type AILibraryApprovalRequest = Schemas['ApprovalRequestItem'];
export type ApprovalDecisionResult = Schemas['ApprovalDecisionResult'];
/** `id` is a BIGINT sent as a JSON number on this surface. */
export type AILibraryCommitment = Schemas['CommitmentItem'];
export type CommitmentStatusResult = Schemas['CommitmentStatusResult'];
/** `GET /ai-library/usage/runs` row. `agent_slug` / `agent_name` are absent
 * (not null) when the agent lookup found nothing. `started_at` is Python's
 * `str(datetime)` — a space, not a `T`, between date and time. */
export type UsageRunItem = Schemas['AiLibraryUsageRunItem'];
export type UsageRunsPage = Schemas['AiLibraryUsageRunsPage'];
/** `GET /ai-library/usage/daily` row. Not `usageService`'s `UsageDailyRow`,
 * which is the team `/usage/summary` row. */
export type AiLibraryUsageDailyRow = Schemas['AiLibraryUsageDailyRow'];
export type UsageDailySummary = Schemas['AiLibraryUsageDaily'];
export type UsageGroupBy = UsageDailySummary['group_by'];
/** `GET /ai-library/usage/summary` — points rollup from `ai_usage_logs`. */
export type AILibraryUsageSummary = Schemas['AiLibraryUsageSummary'];
export type ChatAttachmentUpload = Schemas['ChatAttachmentUpload'];
// —— end AI Library ——

// ── P5 settings / legacy skills ──
// `/api/v1/settings/*`. The legacy `/api/v1/skills` CRUD was removed in P5
// (no caller); skills live under `/ai-library/skills` (see the AI Library block).
export type UserSettingsResponse = Schemas['UserSettingsResponse'];
/** Presence and validity only — cookie content never comes back. */
export type CookieStatus = Schemas['CookieStatusItem'];
export type CookieListResponse = Schemas['CookieListResponse'];
/** `headers_text` is `""` with no row for the platform and `null` when the row
 * holds a cookie but headers were never saved. */
export type PlatformHeaders = Schemas['SettingsPlatformHeaders'];
export type SettingsPlatformWriteResult = Schemas['SettingsPlatformWriteResult'];
// —— end P5 settings ——

// ── P5 ai ──
// `/api/v1/ai/*` triggers, capability board, governance, platform models and
// `/api/v1/ai/memory` writes.
/** `POST /ai/transcribe/resource/{id}`. `extracting_audio` only on the queued
 * answer, `blocking_task_id` only when an audio-only extraction holds the
 * slot; both are absent (not null) on every other branch. */
export type TranscribeTriggerResponse = Schemas['AiTranscribeTriggerResponse'];
/** `POST /ai/summarize/resource/{id}`. `platform_id` is absent on the
 * "already in progress" answer from the dedup SELECT. */
export type SummarizeTriggerResponse = Schemas['AiSummarizeTriggerResponse'];
/** `POST /ai/analyze/resource/{id}`. `platform_id` only when queued. */
export type AnalyzeTriggerResponse = Schemas['AiAnalyzeTriggerResponse'];
export type LegacyTranscribeTriggerResponse = Schemas['AiLegacyTranscribeTriggerResponse'];
export type LegacySummarizeTriggerResponse = Schemas['AiLegacySummarizeTriggerResponse'];
/** `POST /ai/analyze/backfill-embeddings`. Resource ids and `space.id` are
 * strings (Snowflake > 2^53). `dispatched` / `in_flight` are always `[]` / `0`
 * since mig 499 (kept for old readers). */
export type BackfillResult = Schemas['AiBackfillEmbeddingsResponse'];
export type CapabilityHealth = Schemas['AiCapabilityHealthRow'];
/** `GET /ai/governance`: one bool per governed module + the Nous switches. */
export type AIGovernanceFlags = Schemas['AiGovernanceResponse'];
/** `GET /ai/nous-models` row. `id` is a BIGINT sent as a JSON number. No
 * key, host or upstream provider — `is_local` is the one derived bit. */
export type NousModelPublic = Schemas['AiNousModelPublic'];
/** `platform_models[<name>]` in `GET/PUT /ai/settings` (spec 2026-09-25
 * §3.1): what a picker shows for one platform row. No key / host / upstream
 * provider; `status` never carries `fail` — failed rows are not listed. */
export type PlatformModelEntry = Schemas['AiPlatformModelEntry'];
export type PlatformModelStatus = PlatformModelEntry['status'];
export type PlatformModelType = PlatformModelEntry['type'];
/** nous-engine reachability behind the platform list. `reachable=false` is
 * "could not read", never "no models". */
export type PlatformEngineState = Schemas['AiPlatformEngineState'];
/** One row of `GET /ai/platform-status`. */
export type PlatformModelRuntime = Schemas['AiPlatformModelRuntime'];
/** `GET /ai/platform-status`: runtime state layered over the settings list. */
export type PlatformStatusResponse = Schemas['AiPlatformStatusResponse'];
export type MemoryPrefsResult = Schemas['AiMemoryPrefsResponse'];
export type MemoryCardResult = Schemas['AiMemoryCardResponse'];
export type MemoryForgetResult = Schemas['AiMemoryForgetResponse'];
// —— end P5 ai ——

// ── P5 media (B) ──
// `/api/v1/media/pending`, `/media/retry/{platform_id}`, and the slides /
// lyrics JSON routes. The file routes (`/media/download/*`, `/slides/{file}`,
// `/audio`) return bytes and have no alias.
/** One `parsed_media` row, `SELECT *` shape. `id` is a BIGINT sent as a JSON
 * number; timestamps are `isoformat()` strings (`+00:00`, not `Z`). */
export type ParsedMediaRow = Schemas['ParsedMediaRow'];
export type PendingDownloadsResponse = Schemas['PendingDownloadsResponse'];
/** `task_id` is null when nothing was dispatched (already running → the retry
 * subscribed), or `"background"` when DBOS dispatch fell back. */
export type RetryDownloadResponse = Schemas['RetryDownloadResponse'];
export type MediaSlide = Schemas['MediaSlide'];
export type MediaSlidesResponse = Schemas['MediaSlidesResponse'];
/** `GET /media/{id}/lyrics` and `POST .../lyrics/fetch`; empty lyrics are
 * `{ lrc: "", lines: [] }`, never null. */
export type MediaLyrics = Schemas['MediaLyricsResponse'];
export type MediaLyricLine = Schemas['MediaLyricLine'];
// —— end P5 media (B) ——

// ── P5 media (A) ──
// `/api/v1/media` CRUD (list, detail, delete, search, statistics, logs,
// cleanup), `/media/fetch`, `/media/fetch/batch`, `/media/{platform_id}/fetch`,
// `/media/{platform_id}/extract-audio`, `/media/soda/playlist/download`.
/** Library card: `parsed_media` card columns + the caller's `resource_id` /
 * `has_prompt`. Ids are JSON numbers; timestamps `isoformat()` strings. */
export type MediaCard = Schemas['MediaCard'];
export type MediaCardList = Schemas['MediaCardListResponse'];
/** `GET /media/{platform_id}`; `video.resource_id` only when the caller owns one. */
export type MediaDetailResult = Schemas['MediaDetailResponse'];
export type MediaDeleteResult = Schemas['MediaDeleteResponse'];
export type MediaStatistics = Schemas['MediaStatistics'];
/** One `user_logs` row. `id` is a JSON number; the video id key is `aweme_id`. */
export type MediaUserLog = Schemas['MediaUserLog'];
export type MediaUserLogsPage = Schemas['MediaUserLogsResponse'];
/** `POST /media/fetch` answers with exactly one of these three shapes. */
export type MediaFetchOwned = Schemas['MediaFetchOwnedResponse'];
export type MediaFetchDedup = Schemas['MediaFetchDedupResponse'];
export type MediaFetchSubmitted = Schemas['MediaFetchSubmittedResponse'];
export type MediaFetchResult = MediaFetchOwned | MediaFetchDedup | MediaFetchSubmitted;
/** `POST /media/fetch/batch`: the inline shape, or — only with
 * `use_celery: true` — the queued shape. */
export type MediaBatchFetchResult = Schemas['MediaBatchFetchResponse'];
export type MediaBatchDispatched = Schemas['MediaBatchDispatchedResponse'];
export type MediaTypeFetchResult = Schemas['MediaTypeFetchResponse'];
export type MediaExtractAudioResult = Schemas['MediaExtractAudioResponse'];
/** `success` is false when no track could be dispatched; `flow_id` may be null. */
export type MediaSodaDownloadResult = Schemas['MediaSodaDownloadResponse'];
// —— end P5 media (A) ——

// ── P6 scenes/shots ──
// `/api/v1/scripts/{id}/scenes`, `/api/v1/scenes/*` and `/api/v1/scenes/{id}/shots`,
// `/api/v1/shots/{id}`. Unlike `/scripts/projects`, these routers stringify
// every bigint id (#1809), so ids are `string` here. `editor/sceneService`
// normalizes the rows further (SceneDoc, Shot) — callers use those.
/** A `script_scenes` row. List / get add `scene_no_in_episode`, and PATCH adds
 * it when nothing was written; `toSceneDoc` passes it through untyped. */
export type ScriptSceneWire = Schemas['ScriptSceneResponse'];
/** `POST /scenes/{id}/elements/ops` success payload. */
export type ScriptSceneOpsResult = Schemas['ScriptSceneOpsResult'];
/** 409 body of `/elements/ops` (the editor rebases onto it). */
export type ScriptSceneVersionConflict = Schemas['ScriptSceneVersionConflict'];
/** `POST /scenes/{id}/copilot-ops`: `proposal` is only present (true) for a stale read. */
export type ScriptSceneCopilotOps = Schemas['ScriptSceneCopilotOps'];
/** A `script_shots` row (status is a plain string on the wire). */
export type StoryboardShotWire = Schemas['StoryboardShotResponse'];
/** The flat `{ success, task_id }` body of `POST /scenes/{id}/auto-storyboard`. */
export type StoryboardTaskDispatch = Schemas['StoryboardTaskDispatch'];
// —— end P6 scenes/shots ——

// ── P6 tags/search/topics/inspiration ──
// Tag group writes (`/api/v1/tags/groups*`) are platform-admin only since P6;
// `GET /tags/groups` stays open. `GET /search/quick` was removed (no caller).
/** A `tag_groups` row as `/tags/groups` renders it: the id is stringified and
 * there is no `created_at` (the old hand-written copy claimed one). */
export type TagGroup = Schemas['TagGroupItem'];
/** `PUT /tags/groups/reorder` and `DELETE /tags/groups/{id}`. */
export type TagGroupMutationResult = Schemas['TagGroupMutationResult'];
/** `POST /topics/{hotspot_id}/generate-script`: the script_ai outline. */
export type TopicScriptResponse = Schemas['TopicScriptResponse'];
export type TopicScriptChapter = Schemas['TopicScriptChapter'];
/** A row of `GET /inspiration/notes/activity`; `day` is `YYYY-MM-DD`. */
export type InspirationNoteActivityDay = Schemas['InspirationNoteActivityDay'];
/** A row of `GET /inspiration/notes/tags`. */
export type InspirationNoteTagCount = Schemas['InspirationNoteTagCount'];
// —— end P6 tags/search/topics/inspiration ——

// ── P6 scripts (beats / memos / versions / editor dispatch) ──
// Beats and commits carry Snowflake ids as JSON numbers on the wire; the editor
// services (`editor/sceneService.ts`) stringify them at the boundary and export
// the normalized `Beat` / `ScriptCommit`. Memo ids are already strings.
/** A `script_beats` row as `/scripts/{id}/beats` and `/beats/{id}` send it. */
export type ScriptBeatWire = Schemas['ScriptBeatRow'];
/** A `beat_memos` row (ids stringified server-side). */
export type BeatMemo = Schemas['BeatMemoOut'];
/** `POST /scripts/{id}/memos/upload` payload. */
export type BeatMemoImageUpload = Schemas['BeatMemoImageUpload'];
/** `GET /scripts/{id}/commits` row (`POST` returns the same row without `author_name`). */
export type ScriptCommitWire = Schemas['ScriptCommitListItem'];
export type ScriptCommitSceneSnapshot = Schemas['ScriptCommitSceneSnapshot'];
/** A scene added / removed in a diff, with its last author. */
export type ScriptCommitSceneChange = Schemas['ScriptCommitSceneChange'];
export type ScriptCommitElementChangeWire = Schemas['ScriptCommitElementChange'];
export type ScriptCommitSceneDiffWire = Schemas['ScriptCommitSceneDiff'];
export type ScriptCommitDiffWire = Schemas['ScriptCommitDiff'];
/** `POST .../commits/{id}/rollback`: a partial failure is still a 200. */
export type ScriptCommitRollback = Schemas['ScriptCommitRollback'];
export type ScriptCommitRollbackSceneResult = Schemas['ScriptCommitRollbackSceneResult'];
/** Flat `{ success, task_id }` of expand-chapter / create-branches / convert-to-scenes. */
export type ScriptAiTaskDispatch = Schemas['ScriptAiTaskDispatch'];
/** `POST /scripts/import-screenplay`: the new script id is a string here. */
export type ScriptImportScreenplayDispatch = Schemas['ScriptImportScreenplayDispatch'];
// —— end P6 scripts ——

// ── P6 shares ──
// `/api/v1/shares`. Share and comment ids are Snowflake BIGINTs sent as JSON
// numbers. No share shape carries the plaintext password.
/** An owner's `shares` row (`POST /shares`, `GET /shares` items). */
export type Share = Schemas['ShareRow'];
/** `DELETE /shares/{id}` toggles active ↔ inactive; no `data` key. */
export type ShareStatusToggle = Schemas['ShareStatusToggleResponse'];
/**
 * `POST /shares/code/{code}`: what a visitor sees. Pass `access_token` as
 * `share_token` to the media and comment routes — a password-protected share
 * accepts nothing else. The resource keys are absent when the share has none.
 */
export type ShareVisitorView = Schemas['ShareVisitorView'];
/** A review comment as `GET /shares/code/{code}/comments` lists it. */
export type ShareComment = Schemas['ShareComment'];
/** The full row `POST /shares/code/{code}/comments` returns to its author. */
export type ShareCommentRow = Schemas['ShareCommentRow'];
// —— end P6 shares ——

// ── P7 reviews ──
// `/api/v1/reviews` (resource review panel). Comment, annotation, resource and
// version ids are Snowflake BIGINTs sent as JSON numbers; timestamps keep the
// `+00:00` form. `status` / `tool_type` are plain strings on the wire (the
// columns carry no CHECK) — narrow them where a closed set is needed.
// (`ReviewComment` above is the project-file comment, a different table.)
export type ResourceReviewAnnotation = Schemas['ReviewAnnotationRow'];
/** A bare `review_comments` row (`resolve` / `reopen`). */
export type ResourceReviewComment = Schemas['ReviewCommentRow'];
/** `POST /reviews/comments`: the new row with its annotations. */
export type ResourceReviewCommentCreated = Schemas['ReviewCommentWithAnnotations'];
/** A top-level comment from `GET /reviews/comments`, replies included. */
export type ResourceReviewThread = Schemas['ReviewCommentThread'];
export type ResourceReviewStatus = Schemas['ReviewStatusRow'];
// —— end P7 reviews ——

// ── P7 conversations ──
// `/api/v1/conversations` (team chat). None of these bodies carries the
// `{success, data}` envelope. Snowflake ids here are already strings on the
// wire (`id`, `promoted_resource_id`); user and agent ids are UUID strings.
/** One member row from `GET /conversations/{id}/members` (a user or an agent). */
export type ConversationMember = Schemas['MemberOut'];
/** `POST /conversations/{id}/members`: rows inserted (existing members skip). */
export type ConversationMembersAdded = Schemas['ConversationMembersAddedResponse'];
/** `DELETE /conversations/{id}/members/{userId}` (remove, or leave when self). */
export type ConversationMemberRemoved = Schemas['ConversationMemberRemovedResponse'];
export type ConversationMemberRole = Schemas['ConversationMemberRoleResponse'];
export type ConversationOwnerTransfer = Schemas['ConversationOwnerTransferResponse'];
/** `POST /conversations/{id}/agents`: only agents the caller can see (P7). */
export type ConversationAgentAdded = Schemas['ConversationAgentAddedResponse'];
/** `removed: false` when the agent was not in the conversation. */
export type ConversationAgentRemoved = Schemas['ConversationAgentRemovedResponse'];
export type ConversationDissolved = Schemas['ConversationDissolveResponse'];
export type ConversationMarkRead = Schemas['ConversationMarkReadResponse'];
/** `POST /conversations/{id}/attachments`: the staged image (id as a string). */
export type ConversationImageUpload = Schemas['ConversationAttachmentUploadResponse'];
export type ConversationAttachmentPromoted = Schemas['ConversationAttachmentPromoteResponse'];
// —— end P7 conversations ——

// ── P7 distribution / libraries / ideation ──
// `/api/v1/libraries`: `id` is a Snowflake BIGINT sent as a JSON **number**
// (`scope_id` is TEXT, a string). Ideation topic ids are strings on the wire.
export type Library = Schemas['LibraryRow'];
export type IdeationTopic = Schemas['IdeationTopic'];
export type IdeationTopicStatus = IdeationTopic['status'];
/** One cached 「选择音乐」 tab; kind + category_id together are its identity. */
export type MusicChart = Schemas['DistributionMusicChart'];
export type MusicChartTrack = Schemas['DistributionMusicChartTrack'];
export type MusicChartsPage = Schemas['DistributionMusicChartsPage'];
/** Always 200: a failed harvest is `success: false` + `detail.reason`. */
export type MusicHarvestResult = Schemas['DistributionMusicHarvestResult'];
/** `POST /distribution/accounts/{id}/refresh`: the whole public row. */
export type SocialAccountRow = Schemas['DistributionAccountRow'];
// —— end P7 distribution / libraries / ideation ——

// ── P7 auth / beat-templates ──
// The web app signs in through supabase-js directly; of `/api/v1/auth/*` it
// only reads the media token.
/** `POST /auth/media-token`: `expires_at` is a Unix timestamp in seconds. */
export type AuthMediaToken = Schemas['AuthMediaToken'];
/** `/api/v1/beat-templates`: `id` is a Snowflake BIGINT sent as a JSON **number**. */
export type BeatTemplateRow = Schemas['BeatTemplateRow'];
/** Stored JSONB anchor: every key may be absent on a hand-edited row. */
export type BeatTemplateAnchorRow = Schemas['BeatTemplateAnchorRow'];
// —— end P7 auth / beat-templates ——

// —— P8 admin: credits ——
/** `POST /points/admin/adjust` (Billing page, platform admins only): the team's
 * balance after the change. A debit larger than the balance stops at 0. */
export type AdminTeamPointsAdjustResult = Schemas['AdminTeamPointsAdjustResult'];
// —— end P8 admin: credits ——

// —— P9 workflows ——
// `/api/v1/workflows` hosts two routers. Templates: Snowflake ids are strings,
// timestamps `isoformat()` strings, `{success, data}` envelope.
export type WorkflowTemplateSummary = Schemas['WorkflowTemplateSummary'];
/** Raw template node: `events` / `form_schema` are the stored JSONB, untyped. */
export type WorkflowTemplateNodeRow = Schemas['WorkflowTemplateNodeRow'];
export type WorkflowTemplateDetail = Schemas['WorkflowTemplateDetail'];
/** One row of the read-only 11-node workflow node bank. */
export type StageLibraryItem = Schemas['WorkflowStageLibraryEntry'];
/** A template node after `workflowService.normalizeTemplateNode`: the JSONB
 * columns narrowed, legacy keys defaulted. Wire → this only through there. */
export type WorkflowTemplateNode = Omit<WorkflowTemplateNodeRow, 'events' | 'form_schema'> & {
  events: WorkflowNodeEvents;
  form_schema: FormFieldDef[];
};
/** A list row (no `nodes`), or a detail normalized by `workflowService`. */
export type WorkflowTemplate = WorkflowTemplateSummary & { nodes?: WorkflowTemplateNode[] };
// DBOS runs: timestamps are Unix epoch **ms** numbers, not ISO strings; bodies
// carry no envelope.
export type DbosWorkflowStep = Schemas['DbosWorkflowStep'];
/** `GET /workflows/{id}/status`. Each SSE `event: status` push is the same
 * snapshot, plus `steps` when the stream was opened with `include_steps`. */
export type DbosWorkflowSnapshot = Schemas['DbosWorkflowSnapshot'] & { steps?: DbosWorkflowStep[] };
/** A DBOS status string (PENDING, ENQUEUED, SUCCESS, ERROR, …). Not a closed
 * set: DBOS adds states between releases. */
export type DbosWorkflowStatus = NonNullable<DbosWorkflowSnapshot['status']>;
export type DbosWorkflowSteps = Schemas['DbosWorkflowSteps'];
export type DbosWorkflowCancelResult = Schemas['DbosWorkflowCancelResult'];
export type DbosWorkflowResumeResult = Schemas['DbosWorkflowResumeResult'];
export type DbosWorkflowRestartResult = Schemas['DbosWorkflowRestartResult'];
// —— end P9 workflows ——
// —— P9 workforce/tasks/issues ——
/** `GET /workforce/board`. `recent_runs[].id` is an `agent_runs` Snowflake sent
 * as a JSON **number**; `slug` / `name` are nullable columns. */
export type WorkforceBoard = Schemas['WorkforceBoard'];
export type WorkforceAgentEntry = Schemas['WorkforceBoardAgent'];
export type WorkforceWorkerRow = Schemas['WorkforceWorkerRow'];
export type WorkforceQueueCounts = Schemas['WorkforceQueueCounts'];
export type WorkforceRecentRun = Schemas['WorkforceBoardRun'];
export type WorkforceStateHistoryRow = Schemas['WorkforceStateHistoryRow'];
/** `GET /workforce/agents/{slug}/detail` (platform admin only). */
export type WorkforceAgentDetail = Schemas['WorkforceAgentDetail'];
export type WorkforceInboxRow = Schemas['WorkforceInboxRow'];
export type WorkforceOutboxRow = Schemas['WorkforceOutboxRow'];
export type WorkforceDetailRun = Schemas['WorkforceDetailRun'];
export type WorkforceInboxClearResult = Schemas['WorkforceInboxClearResult'];
/** `GET /workforce/tasks/by-inbox/{id}`: `task` is null until the recipient
 * picks the Delegate up; `outbox_response` is null until the task is terminal. */
export type DelegateTaskLookup = Schemas['WorkforceDelegateTaskLookup'];
export type DelegateTaskRow = Schemas['WorkforceDelegateTask'];
export type DelegateOutboxResponse = Schemas['WorkforceDelegateOutboxResponse'];
/** `GET /issues/{id}/progress` — computed from the runs, never from
 * `execution_state` alone. */
export type IssueProgress = Schemas['IssueRollup'];
export type IssueProgressRun = Schemas['IssueRollupRun'];
/** One row of `GET /issues/{id}/schedules`. */
export type IssueScheduleItem = Schemas['IssueScheduleItem'];
export type PipelineRun = Schemas['PipelineRun'];
export type PipelineRunList = Schemas['PipelineRunListResponse'];
/** `POST /flows/{id}/cancel`: only the keys of the branch taken are present. */
export type FlowCancelResult = Schemas['FlowCancelResult'];
export type ScheduleFireNowResult = Schemas['ScheduleFireNowResult'];
// —— end P9 workforce/tasks/issues ——
// —— P9 codex/keys/misc ——
// `/api/v1/codex-daemon/*` ids are strings on the wire; the bodies have no
// `success` key (`{"data": ...}`).
/** One live paired device; `env_report` is whatever the daemon last reported. */
export type CodexDevice = Schemas['CodexDaemonDevice'];
export type CodexDaemonPairCode = Schemas['CodexDaemonPairCode'];
/** `GET /jimeng-cli/status` `data`: logged in (with credits) or logged out
 * (with a `reason`); narrow on `logged_in`. */
export type JimengCliStatus = Schemas['JimengCliStatusEnvelope']['data'];
/** `POST /jimeng-cli/login` `data` (platform admins only): a device-flow link,
 * or `already_logged_in` when the CLI reused its token. */
export type JimengCliLoginResult = Schemas['JimengCliLoginEnvelope']['data'];
// —— end P9 codex/keys/misc ——
// —— P9 payment/cleanup/probes ——
// `/api/v1/payment/*`: `{ success, data }` envelopes. Order `id` / `team_id`
// are Snowflake BIGINTs sent as JSON **numbers** (package ids are uuid strings).
/** One purchasable package from `GET /payment/packages`. */
export type PointPackage = Schemas['PaymentPackageRow'];
/** One `orders` row (`GET /payment/orders`, `POST /payment/create-order`). */
export type PaymentOrder = Schemas['PaymentOrderRow'];
/** `GET /payment/order/{id}/status`. */
export type PaymentOrderStatus = Schemas['PaymentOrderStatus'];
export type PaymentPackagesResponse = Schemas['Envelope_list_PaymentPackageRow__'];
export type PaymentOrdersResponse = Schemas['Envelope_list_PaymentOrderRow__'];
export type PaymentOrderResponse = Schemas['Envelope_PaymentOrderRow_'];
export type PaymentOrderStatusResponse = Schemas['Envelope_PaymentOrderStatus_'];
// `/api/v1/cleanup/*`: bare bodies (no envelope). Media ids are Snowflake
// BIGINTs sent as JSON **numbers**.
/** `POST /cleanup/media/{id}/action` and `POST|DELETE /cleanup/media/{id}/keep`. */
export type CleanupMediaActionResult = Schemas['CleanupMediaActionResult'];
/** `POST /cleanup/batch`: `failed_ids` = media the caller owns no resource for. */
export type CleanupBatchResult = Schemas['CleanupBatchResult'];
/** `GET /cleanup/storage`: over the caller's non-trashed media only. */
export type CleanupStorageBreakdown = Schemas['CleanupStorageBreakdown'];
// `/api/v1/cover-templates`: `{ data }` envelopes (no `success`). Folder and
// resource ids are Snowflake STRINGS here; `last_used_at` is `+00:00` ISO.
export type CoverTemplateFolder = Schemas['CoverTemplateFolderOut'];
export type CoverTemplate = Schemas['CoverTemplateOut'];
export type CoverTemplateList = Schemas['CoverTemplateListOut'];
/** `POST /cover-templates/use`: how many ids were counted. */
export type CoverTemplateUsed = Schemas['CoverTemplateUseOut'];
// `/api/v1/collections`: `collection_id` echoes the path (string); preset
// ids are Snowflake BIGINTs sent as JSON **numbers**.
export type CollectionRefreshResult = Schemas['CollectionRefreshResult'];
/** `presets` is absent when the user already had presets. */
export type CollectionInitPresetsResult = Schemas['CollectionInitPresetsResult'];
// —— end P9 payment/cleanup/probes ——
// —— P9 collections row (id is a BIGINT: JSON number) ——
export type SmartCollectionRow = Schemas['CollectionResponse'];
export type SmartCollectionMediaPage = Schemas['CollectionMediaResponse'];
// —— PR 3 shot index: `/api/v1/resources/{id}/shots`, `/api/v1/ai/analyze/index-shots/{id}`,
// `/api/v1/ai/analyze/backfill-shots`. Ids are Snowflake STRINGS on this
// surface; every `*_ms` field is an integer millisecond count.
export type ShotOut = Schemas['ShotOut'];
export type ShotIndexInfo = Schemas['ShotIndexInfo'];
export type ShotsResponse = Schemas['ShotsResponse'];
export type IndexShotsResponse = Schemas['IndexShotsResponse'];
export type BackfillShotsResponse = Schemas['BackfillShotsResponse'];
export type BackfillShotsSkip = Schemas['BackfillShotsSkip'];
