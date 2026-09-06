
export enum DownloadStatus {
  PENDING = 'PENDING',
  PROCESSING = 'PROCESSING',
  COMPLETED = 'COMPLETED',
  FAILED = 'FAILED',
  SKIPPED = 'SKIPPED'
}

// AI processing status for transcript/summary/visual analysis
export type AIStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'skipped';

export interface ParsedMedia {
  // Media Identity
  id?: string;  // Snowflake BIGINT primary key (was UUID, migrated in 059)
  platform_id: string;
  /** @deprecated — parsed_media is now global, user_id will be removed */
  user_id?: string;

  // Platform info
  source_platform?: string;  // 'douyin', 'youtube', 'bilibili', 'twitter', 'other'

  // Interaction Data
  like_count?: number;
  comment_count?: number;
  share_count?: number;
  favorite_count?: number;

  // Metadata
  original_url: string;
  duration?: string; // seconds
  resolution?: string;
  datasize?: string; // formatted size string (e.g., "3.15 MB")
  datasize_bytes?: number; // raw size in bytes
  hashtags?: string;
  published_at?: string; // datetime string
  author?: string;
  title?: string;
  media_type?: string;
  description?: string;

  // Download Info
  video_download_urls?: string[];
  image_download_urls?: string[];

  // Audio Info
  music_download_path?: string;   // BGM downloaded from separate music URL
  extract_audio_path?: string;    // audio track extracted from the video itself
  extract_audio_status?: 'pending' | 'processing' | 'completed' | 'failed' | 'skipped';
  music_name?: string;

  // Cover Info
  cover_urls?: string[];
  dynamic_cover_url?: string;
  cover_download_status?: DownloadStatus;
  cover_download_path?: string;

  // Tracking
  video_download_status?: DownloadStatus;
  music_download_status?: DownloadStatus;
  image_download_status?: DownloadStatus;
  image_download_path?: string;
  download_duration?: number;
  download_path?: string;
  error_message?: string;
  download_time?: string;

  // User Data (rating/notes are in resources table, not parsed_media)
  tags?: string[];

  // AI Processing Status
  transcript_status?: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
  summary_status?: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
  visual_analysis_status?: 'none' | 'pending' | 'processing' | 'completed' | 'failed';

  // Does the owning resource carry an AI prompt? Computed server-side
  // (MediaRepository.has_prompt_expr) from gen_prompt / gen_prompt_zh /
  // slide_prompts, so the card's Prompt icon doesn't have to pull the
  // prompt text (capped at 20k chars per column) for every row.
  has_prompt?: boolean;

  // AI Generated Content (legacy)
  ai_extract_text?: string;
  ai_rewrite_text?: string;
  ai_analyze_text?: string;
  ai_generated_at?: string;

  // HLS Streaming
  hls_path?: string;
  media_format?: 'mp4' | 'hls';

  // Summary preview (joined from resource_summaries)
  summary_text?: string;

  // Resource linkage (joined from resources table via media_id)
  resource_id?: string;

  // Soda / audio metadata (jsonb on parsed_media, present at runtime via
  // parsed_media!inner(*) join but not always typed). Shape is platform-specific.
  metadata?: {
    quality?: { Quality?: string; Format?: string; Bitrate?: number };
    chorus?: { start?: number; duration?: number };
    lyrics?: { lrc?: string; lines?: Array<{ text?: string; line_start_ms?: number }> };
    // Soda (qishui) own color palette — drives the themed audio player / detail page.
    colors?: {
      playing_wave_color?: { rgb?: string; alpha?: string };
      paused_wave_color?: { rgb?: string; alpha?: string };
      playing_lyric_color?: { rgb?: string; alpha?: string };
      normal_lyric_color?: { rgb?: string; alpha?: string };
      background_color?: { rgb?: string; alpha?: string };
      cover_gradient_effect_color?: Array<{ rgb?: string; alpha?: string }>;
      base_colors?: Array<{ rgb?: string; alpha?: string }>;
    };
    [k: string]: unknown;
  } | null;

  // Timestamps
  created_at?: string;
  updated_at?: string;
}

// Backward-compat alias — allows existing components to keep using `Video`
// TODO: Remove after all component files are migrated to ParsedMedia
export type Video = ParsedMedia;

// AI Transcript/Summary Data
export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  /** Speaker-diarization label (e.g. "S01"), present only for providers that
   *  diarize (self-hosted moss-asr). Absent on old transcripts and the
   *  OpenAI / Volcengine paths. */
  speaker?: string;
}

export interface TranscriptData {
  text: string;
  segments: TranscriptSegment[];
  language: string;
  duration: number;
  created_at: string;
}

export interface SummaryData {
  summary: string;
  key_points: string[];
  topics: string[];
  created_at: string;
}

// Keep backward compatibility alias
export type DouyinBase = ParsedMedia;

export type ViewState = 'parser' | 'dashboard' | 'settings' | 'cleanup' | 'points' | 'mediatrack' | 'resources' | 'members' | 'billing' | 'todolist' | 'shared' | 'agents' | 'skills' | 'ailibrary' | 'chat' | 'distribution' | 'canvas';

export interface ApiKey {
  id: number;
  key_id: string;        // Identifier for API calls
  key_prefix: string;    // Display prefix like dk_xxxx...
  key_value?: string;    // Masked display form (prefix + …). Full key is shown only once, at creation.
  key_value_set?: boolean; // Whether a full key is stored for this record
  name: string;
  description?: string;
  status: 'active' | 'revoked';
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  last_used_at?: string | null;
  usage_count?: number;
  rate_limit?: number | null;
  scopes: string[];
}

export interface UserSettings {
  downloadPath: string;
  supabaseUrl?: string;
  supabaseAnonKey?: string;
  apiUrl?: string;
  apiKey?: string;
  progressStyle?: 'neon' | 'wave';
  // Max simultaneous downloads per user (batch concurrency cap, 1..20)
  maxConcurrentDownloads?: number;
  // Transcode settings
  transcodeEnabled?: boolean;
  transcodeTiers?: string;
  ffmpegEncoder?: string;
  ffmpegPreset?: string;
  transcodeParallelTiers?: boolean;
}

export interface UserProfile {
  name: string;
  email: string;
  avatarUrl: string;
  plan: string;
  role?: 'admin' | 'user' | 'test';
}

// API Response types
export interface ApiResponse<T = unknown> {
  success: boolean;
  message?: string;
  data?: T;
}

export interface MediaListResponse {
  success: boolean;
  count: number;
  media: ParsedMedia[];
}

// Backward-compat alias
export type VideoListResponse = MediaListResponse;

export interface StatisticsResponse {
  success: boolean;
  statistics: {
    total: number;
    pending: number;
    completed: number;
    failed: number;
    skipped: number;
  };
}

// Team types
export interface Team {
  id: string;
  name: string;
  owner_id: string;
  invite_code: string;
  is_personal?: boolean;
  enabled_modules?: string[];
  created_at: string;
}

export interface TeamMember {
  team_id: string;
  user_id: string;
  role: 'owner' | 'admin' | 'member';
  joined_at: string;
  // Joined from auth.users
  email?: string;
  name?: string;
}

// Sidebar modes
export type SidebarMode = 'personal' | 'team' | 'project';

// Permission system
export type Permission =
  | 'project.view'
  | 'project.create'
  | 'project.manage'
  | 'resource.view'
  | 'resource.upload'
  | 'member.view'
  | 'member.manage'
  | 'billing.view'
  | 'review.view'
  | 'review.approve';

// Collection types
export interface Collection {
  id: string;
  name: string;
  owner_id: string;
  team_id: string | null;
  created_at: string;
  // Computed
  media_count?: number;
  /** @deprecated Use media_count */
  video_count?: number;
  is_shared?: boolean;
  thumbnail_url?: string; // First media's cover
}

export interface CollectionMedia {
  collection_id: number;
  media_id: number;
  added_by: string;
  added_at: string;
}

// Backward-compat alias
export type CollectionVideo = CollectionMedia;

// Library (team-scoped resource library)
export interface Library {
  id: string;
  name: string;
  scope_type: 'team';
  scope_id: string;
  created_by: string;
  icon: string | null;
  color: string | null;
  sort_order: number;
  visibility: 'inherited' | 'restricted';
  created_at: string;
  updated_at: string;
}

// Folder (virtual folder tree)
export interface Folder {
  id: string;
  name: string;
  parent_id: string | null;
  library_id: string | null;
  scope_id: string;
  created_by: string;
  sort_order: number;
  is_system: boolean;
  // Stable identity of a system folder ('cover_templates', 'chat_uploads'),
  // independent of its display name — migration 441 added the column and
  // `folders` is fetched with `select('*')`, so it is always on the wire.
  // Optional here only because hand-written Folder fixtures predate it.
  system_key?: string | null;
  icon: string | null;
  color: string | null;
  visibility: 'inherited' | 'restricted';
  is_smart: boolean;
  smart_rules: Record<string, unknown> | null;
  is_trashed: boolean;
  trashed_at: string | null;
  created_at: string;
  updated_at: string;
  // Computed
  children?: Folder[];
  resource_count?: number;
}

// Resource (core resource record)
export interface GenParams {
  tool?: 'comfyui' | 'a1111' | 'nous' | string;
  provider?: string;
  model?: string;
  model_hash?: string;
  sampler?: string;
  scheduler?: string;
  steps?: number;
  cfg?: number;
  seed?: number;
  denoise?: number;
  width?: number;
  height?: number;
  size?: string;
  aspect_ratio?: string;
  loras?: string[];
  text_encoder?: string;
  vae?: string;
}

export interface Resource {
  id: string;
  creator_id: string;
  source_type: 'web' | 'upload';
  media_id: string | null;
  filename: string;
  file_type: string | null;
  mime_type: string | null;
  file_path: string | null;
  file_size_bytes: number | null;
  duration_seconds: number | null;
  resolution: string | null;
  audio_bitrate_kbps?: number | null;
  chorus_start_ms?: number | null;
  lyrics_json?: { lrc: string; lines: Array<{ text: string; line_start_ms: number | null }> } | null;
  thumbnail_path: string | null;
  cover_image_path: string | null;
  current_version: number;
  notes: string | null;
  gen_prompt: string | null;
  gen_prompt_zh?: string | null;
  gen_prompt_negative?: string | null;
  gen_prompt_negative_zh?: string | null;
  slide_prompts?: Record<string, { en?: string; zh?: string; neg_en?: string; neg_zh?: string }> | null;
  gen_prompt_json?: string | null;
  // Normalised AI generation parameters (migration 440). Every key optional;
  // written by upload_postprocess (PNG metadata), promote (generated_media)
  // and the resource_gen_params backfill. Absent/null == unknown.
  gen_params?: GenParams | null;
  url: string | null;
  rating: number; // 0-5
  // Download status fields used to live here as mirrors of parsed_media;
  // PR-C dropped the columns. Read these via the parsed_media join
  // (``resource.parsed_media?.video_download_status`` etc.) when needed.
  // AI Processing Status (moved from ParsedMedia to Resource)
  transcript_status?: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
  summary_status?: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
  visual_analysis_status?: 'none' | 'pending' | 'processing' | 'completed' | 'failed';
  is_trashed: boolean;
  trashed_at: string | null;
  // Last location (for restore after orphan GC)
  last_folder_id?: string | null;
  last_library_id?: string | null;
  last_scope_type?: string | null;
  last_scope_id?: string | null;
  created_at: string;
  updated_at: string;
  /** Number of child images when this resource is a gallery entity
   *  (mime_type='application/x-mediahub-gallery'). 0 / absent otherwise. */
  gallery_count?: number;
  // Joined
  tags?: Tag[];
  folder_name?: string;
  /** Optional join to parsed_media for Source / Social filter chips.
   *  Only the fields needed client-side are projected; expand as needed. */
  media?: {
    id: string | number;
    source_platform?: string | null;
    like_count?: number | null;
    comment_count?: number | null;
    favorite_count?: number | null;
    share_count?: number | null;
  } | null;
}

// Resource item (resource <-> workspace mapping)
export interface ResourceItem {
  id: string;
  resource_id: string;
  scope_id: string;
  folder_id: string | null;
  library_id: string | null;
  added_by: string | null;
  created_at: string;
  // Joined
  resource?: Resource;
}

// Resource version
export interface ResourceVersion {
  id: string;
  resource_id: string;
  version_number: number;
  filename: string | null;
  file_path: string | null;
  file_size_bytes: number | null;
  mime_type: string | null;
  duration_seconds: number | null;
  resolution: string | null;
  thumbnail_path: string | null;
  uploaded_by: string | null;
  notes: string | null;
  hls_path: string | null;
  transcode_status: string | null;
  transcode_at: string | null;
  created_at: string;
}

// Notification types
export interface Notification {
  id: string;
  type: 'system' | 'team';
  title: string;
  content: string | null;
  team_id: string | null;
  created_by: string | null;
  created_at: string;
}

export interface UserNotification {
  user_id: string;
  notification_id: string;
  read_at: string | null;
  // Joined
  notification?: Notification;
}

// Smart Organization Types

// Tags
export interface Tag {
  id: string;  // UUID string from backend
  name: string;
  name_zh?: string | null;  // Chinese name for bilingual support
  color: string | null;
  icon: string | null;
  type: 'system' | 'user' | 'time';
  group_name?: string | null;
  group_id?: string | null;
  enabled?: boolean;
  prompt_trigger?: boolean;
  sort_order?: number;
  user_id?: string;
  video_count?: number;
  media_count?: number;
  origin?: 'curated' | 'note';  // 'note' = shadow tag auto-created from note #tags
  created_at: string;
}

// Smart Collections
export interface SmartCollection {
  id: number | string;
  name: string;
  description?: string | null;
  icon?: string | null;
  color?: string | null;
  rules?: CollectionRules;
  smart_rules?: {
    operator: 'AND' | 'OR';
    match: boolean;
    conditions: Array<{ field: string; op: string; value: string }>;
  };
  is_preset?: boolean;
  is_active?: boolean;
  is_smart?: boolean;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
  video_count?: number;
  created_at?: string;
  updated_at?: string;
}

export interface CollectionCondition {
  field: 'tag' | 'author' | 'date' | 'title' | 'description' | 'media_type' | 'view_count';
  operator: 'equals' | 'contains' | 'starts_with' | 'in' | 'gt' | 'lt' | 'gte' | 'lte';
  value: string | number | string[];
}

export interface CollectionRules {
  match: 'all' | 'any';
  conditions: CollectionCondition[];
}

// Cleanup
export type CleanupReason = 'never_viewed' | 'duplicate_content' | 'old_unused' | 'large_file';

export interface CleanupSuggestion {
  media_id: number;
  title: string;
  cover_url: string | null;
  author: string | null;
  reason: CleanupReason;
  reason_detail: string;
  storage_size: number | null;
  created_at: string;
  last_viewed_at: string | null;
  view_count: number;
  similarity_to: number | null;
  similarity_score: number | null;
}

// Search
export interface SearchResult {
  media_id: number;
  platform_id: string;
  title: string;
  cover_url: string | null;
  author: string | null;
  similarity_score: number;
  description: string | null;
  tags: string[];
  view_count: number;
  created_at: string;
}

// Analysis
export interface MediaAnalysis {
  media_id: number;
  platform_id: string;
  visual_analysis: string | null;
  content_categories: string[];
  detected_objects: string[];
  scene_description: string | null;
  suggested_tags: string[];
  analyzed_at: string | null;
}

// Backward-compat alias
export type VideoAnalysis = MediaAnalysis;

// AI Provider settings
export interface AIProviderConfig {
  enabled: boolean;
  // WRITE-only: type a new key here to set/replace it. Leave blank (or
  // omit) to keep whatever is already stored server-side — the backend
  // never echoes a raw key back (encrypted at rest, secret-at-rest Phase 2),
  // so after a GET/PUT round-trip this field is always empty again.
  api_key?: string;
  // READ-only masking fields returned by GET/PUT instead of the raw key:
  // whether a key is currently stored, its last-4-chars hint, and (for the
  // multi-key BYOK rotation shape) how many keys are configured.
  api_key_set?: boolean;
  api_key_hint?: string;
  api_key_count?: number;
  app_id?: string;
  base_url?: string;
  // Full catalog from "Test Connection" — typically 100+ entries.
  models?: string[];
  // User-curated whitelist: only these models are exposed to agent
  // pickers. Auto-seeded from ``selected_model`` on first read for
  // back-compat with pre-existing accounts.
  enabled_models?: string[];
  // Inverse of enabled_models, used by the platform ("nous") pseudo-provider:
  // a blacklist of model names the user has hidden from the pickers. Blacklist
  // (not whitelist) semantics so any model the admin adds later is visible by
  // default — the user only ever opts models OUT.
  disabled_models?: string[];
  selected_model?: string;
  summary_model?: string;
  analysis_model?: string;
}

export interface AISettings {
  ai_enabled: boolean;
  auto_transcribe?: boolean;
  auto_summarize?: boolean;
  preferred_language: string;
  /** Free-text ASR hotwords / domain hints (names, terms), comma- or newline-
   *  separated. Threaded to the transcription provider as a prompt/context hint. */
  transcription_hotwords?: string;
  // Keyed by provider slug (openai / deepseek / doubao / minimax / kimi /
  // qwen / volcengine / ollama / lmstudio / ...). Open-keyed so adding a
  // provider in PROVIDER_META doesn't require touching this type again.
  providers: Record<string, AIProviderConfig | undefined>;
  task_assignment: {
    transcription: string;  // provider key
    summarization: string;
    visual_analysis: string;
    translation?: string;  // zh↔en prompt translation agent slug
    caption?: string;  // image → prompt reverse-engineering agent slug
    classification?: string;  // 12-dimension auto-tagging agent slug
    image_generation?: string;  // storyboard image provider
    script_generation?: string;  // storyboard script/prompt LLM
  };
  // Last BYOK connection-test result per provider slug — persisted server-side
  // so the Settings UI restores it across reloads. Read-only from the client's
  // perspective (written via /ai/test-connection + /ai/provider-health).
  provider_health?: Record<string, ProviderHealthEntry>;
}

export interface ProviderHealthEntry {
  status: 'ok' | 'fail';
  detail?: string;
  tested_at?: string;  // ISO-8601 UTC
}

/**
 * Per-module governance flags returned by GET /api/v1/ai/governance.
 * true = user may configure; false = admin-managed (hide the UI row).
 * Absent = true (default-open / fail-open).
 */
export interface AIGovernanceFlags {
  chat: boolean;
  transcription: boolean;
  translation: boolean;
  visual_analysis: boolean;
  caption: boolean;
  classification: boolean;
  summarization: boolean;
  /** Global Nous master switch (default false). */
  nous_enabled?: boolean;
  /** Per-module Nous allow flags (default-on once nous_enabled). */
  nous_modules?: Record<string, boolean>;
}

// Nous Platform Model (admin-configured, runs on the platform key)
// Types mirror the DB CHECK constraint (migration 345).
export type NousModelType = 'llm' | 'embedding' | 'tts' | 'asr' | 'image' | 'video';

// NOTE: no ``description`` field — the public endpoint deliberately omits it
// (admin-internal ops notes must not surface in the user-facing UI).
export interface NousModelPublic {
  name: string;
  display_name: string;
  type: NousModelType;
  pricing_type: 'per_hour' | 'per_request' | 'per_token';
  pricing_value: number;
  /**
   * Last hourly connectivity probe (backend scheduled_health). `null` /
   * absent means never probed — which is neither healthy nor broken, so the
   * UI must say nothing rather than assume. `not_probed` (backend migration
   * 428) is the same non-claim for a different reason: the probe has no
   * protocol for that model TYPE and checked nothing. Neither is a failure,
   * and `buildModelHealth` keeps both out of the map.
   *
   * NOTE: the failure REASON TEXT (`last_test_detail`) is deliberately not part
   * of this public payload — probe errors routinely embed the upstream host and
   * private base_url. Users get the status plus the classified code below;
   * admin gets the raw text.
   */
  last_test_status?: 'ok' | 'fail' | 'not_probed' | null;
  last_tested_at?: string | null;
  /**
   * Closed-enum classification of the last FAILED probe (migration 427):
   * `timeout` | `unreachable` | `auth` | `rate_limit` | `model_not_found` |
   * `upstream_error` | `bad_response` | `other`. Typed as a plain string on
   * purpose — the backend enum may grow before a frontend deploy catches up,
   * and `healthReasonKey` already degrades gracefully on a value it doesn't
   * know. `null` / absent = never probed, or probed before the column existed.
   */
  last_test_code?: string | null;
  /**
   * 由后端 `list_enabled` 派生(`mediahub_model_repository`:provider 属于
   * codex-local / jimeng-local),true = 这一行不在平台上跑,而是在**用户自己
   * 电脑**的 daemon 上。UI 据此提示本机链路的限制(纯文本、无工具调用)。
   * 缺省/absent 一律按平台模型处理——没标就不是本机的。
   */
  is_local?: boolean;
}

// Points System Types
export interface PointPackage {
  id: string;
  name: string;
  description: string | null;
  points_amount: number;
  price_cents: number;
  currency: string;
  sort_order: number;
}

export interface TeamQuota {
  team_id: string;
  points_balance: number;
  storage_limit_bytes: number;
  storage_used_bytes: number;
  storage_used_percent: number;
}

export interface PointTransaction {
  id: string;
  team_id: string;
  user_id: string | null;
  amount: number;
  balance_after: number;
  type: 'purchase' | 'consume' | 'refund' | 'gift' | 'admin_adjust';
  reference_type: string | null;
  reference_id: string | null;
  description: string | null;
  created_at: string;
}

export interface PointPricing {
  action_type: string;
  points_cost: number;
  description: string | null;
}

export interface PaymentOrder {
  id: string;
  team_id: string;
  user_id: string;
  package_id: string;
  points_amount: number;
  amount_cents: number;
  currency: string;
  payment_method: 'wechat' | 'alipay';
  payment_status: 'pending' | 'paid' | 'failed' | 'expired' | 'refunded';
  payment_url: string | null;
  trade_no: string | null;
  paid_at: string | null;
  expired_at: string;
  created_at: string;
}

export interface QuotaCheck {
  allowed: boolean;
  points_cost: number;
  current_balance: number;
  reason: string | null;
}


// Project types (MediaTrack)
export interface Project {
  id: string;
  name: string;
  description: string | null;
  owner_id: string;
  team_id: string | null;
  project_type: 'internal' | 'external' | 'personal';
  project_group: string | null;
  announcement: string | null;
  is_starred: boolean;
  color_label: string | null;
  archived_at: string | null;
  file_count: number;
  display_code?: string;
  modules_enabled?: string[];
  // Ideation (M1.5): the topics.id this project was created from (soft
  // pointer, null for a from-scratch project).
  topic_id?: string | null;
  created_at: string;
  updated_at: string;
  // Card enrichment (Phase B B1) — batch-derived on the list endpoint;
  // null/absent when the project has no members/history rows.
  members_preview?: ProjectMembersPreview | null;
  latest_activity?: ProjectCardActivity | null;
  // Workflow badge (M2-W3-3) — batch-derived; null/absent for a No-workflow
  // project (no instance nodes).
  workflow_badge?: ProjectWorkflowBadge | null;
  /** M4 Autopilot (mig 395, task O1): project-level master switch — the
   * `autopilot_tick` engine no-ops entirely when this is false (spec §2 step
   * 1). Real NOT NULL DEFAULT true column, written straight through by
   * `PATCH /projects/{id}` (`ProjectUpdate.autopilot_enabled`, no name
   * mapping unlike `archived`). Optional here (like `modules_enabled` above)
   * so pre-mig-395 literals across the codebase keep compiling untouched —
   * callers should default a missing value to `true`, the server's own
   * default. */
  autopilot_enabled?: boolean;
}

/** Project-card workflow badge (M2-W3-3). Null for No-workflow projects. */
export interface ProjectWorkflowBadge {
  /** Name of the node at the cursor; null when no cursor is set. */
  current_node_name: string | null;
  /** Count of non-skipped nodes. */
  workflow_total: number;
  /** 1-based index of the current node among non-skipped nodes; null when no cursor. */
  workflow_position: number | null;
  /** Running agent_runs on this project. */
  agents_active: number;
}

// ── Ideation topic pool (M1.5) ───────────────────────────────────────────────

export type TopicStatus = 'candidate' | 'shortlisted' | 'produced' | 'archived';

/** Which library a topic's reference points back to (the "回源" target).
 *  'blank' = a hand-written topic with no source. */
export type TopicSource =
  | 'inspiration'
  | 'topic'
  | 'library'
  | 'blank';

/** One ideation-pool topic = cover + title + reference (spec §1 / §9). The
 *  reference is a soft pointer to at most one source; ids stay strings. */
export interface Topic {
  id: string;
  team_id: string;
  title: string;
  cover_url: string | null;
  excerpt: string | null;
  status: TopicStatus;
  note_id: string | null;
  resource_id: string | null;
  media_id: string | null;
  inspiration_topic_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectMembersPreview {
  count: number;
  members: { user_id: string; username: string }[];
}

export interface ProjectCardActivity {
  kind: 'file' | 'stage';
  actor?: string;
  at?: string | null;
  label?: string; // stage: the stage name
  stalled?: boolean;
}

export interface ProjectMember {
  // project_members has a composite PK (project_id, user_id) and NO id column;
  // user_id is the member identifier used on the /members/{member_id} route.
  project_id: string;
  user_id: string;
  role: 'manager' | 'editor' | 'viewer' | 'external';
  invited_by: string | null;
  email?: string;
  joined_at: string;
}

export interface ProjectFolder {
  id: string;
  project_id: string;
  parent_id: string | null;
  name: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export type ReviewStatus = 'pending_review' | 'in_review' | 'feedback_collected' | 'approved';

export interface ProjectShare {
  id: string;
  project_file_id: string | null;
  share_type: string;
  share_code: string;
  password: string | null;
  expires_at: string | null;
  is_active: boolean;
  view_count: number;
  created_at: string;
}

export interface FileVersion {
  id: string;
  file_id: string;
  version_number: number;
  filename: string | null;
  file_path: string | null;
  file_size_bytes: number | null;
  mime_type: string | null;
  duration_seconds: number | null;
  resolution: string | null;
  fps: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  video_bitrate_kbps: number | null;
  audio_bitrate_kbps: number | null;
  chorus_start_ms?: number | null;
  audio_channels: number | null;
  audio_sample_rate: number | null;
  thumbnail_path: string | null;
  cover_image_path: string | null;
  uploaded_by: string | null;
  notes: string | null;
  created_at: string;
}

// Annotation / Drawing types
export interface DrawingData {
  strokes: Stroke[];
  width: number;
  height: number;
}

export interface Stroke {
  id: string;
  tool: 'pen' | 'arrow' | 'rect' | 'circle' | 'text';
  points: { x: number; y: number }[];
  color: string;
  strokeWidth: number;
  text?: string;  // For text tool
}

export interface ReviewComment {
  id: string;
  file_id: string;
  version_id: string | null;
  author_id: string;
  author_email?: string;
  content: string;
  timestamp_seconds: number | null;
  drawing_data?: DrawingData | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectFile {
  id: string;
  project_id: string;
  filename: string;
  file_type: string | null;
  mime_type: string | null;
  file_path: string | null;
  file_size_bytes: number | null;
  media_id: string | null;
  duration_seconds: number | null;
  resolution: string | null;
  fps: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  video_bitrate_kbps: number | null;
  audio_bitrate_kbps: number | null;
  chorus_start_ms?: number | null;
  audio_channels: number | null;
  audio_sample_rate: number | null;
  thumbnail_path: string | null;
  cover_image_path: string | null;
  uploaded_by: string | null;
  notes: string | null;
  is_trashed: boolean;
  trashed_at: string | null;
  review_status: ReviewStatus | null;
  current_version: number;
  created_at: string;
  updated_at: string;
  // Deliverable back-link (M2-W1): the mirror issue a file was filed from, plus
  // its human identifier (MH-N) for the Files module's "from MH-xx" chip.
  source_issue_id?: string | null;
  source_issue_identifier?: string | null;
}

// Share types
export type ShareType = 'link' | 'review' | 'presentation' | 'delivery';
export type ShareStatus = 'active' | 'expired' | 'cancelled';

export interface Share {
  id: string;
  resource_id: string | null;
  project_file_id: string | null;
  folder_id: string | null;
  version_id: string | null;
  share_type: ShareType;
  shared_by: string;
  share_name: string;
  share_code: string;
  password: string | null;
  allow_download: boolean;
  expires_at: string | null;
  max_views: number | null;
  view_count: number;
  watermark: boolean;
  status: ShareStatus;
  created_at: string;
}

// ============================================
// Storyboard Types
// ============================================

export interface StoryboardProject {
  id: string;
  team_id: string;
  created_by: string;
  name: string;
  description?: string;
  cover_image_url?: string;
  viewport_json?: { x: number; y: number; zoom: number };
  settings_json?: Record<string, unknown>;
  project_id?: string;
  display_code?: string;
  status: 'active' | 'archived' | 'deleted';
  created_at: string;
  updated_at: string;
}

export interface ScriptProject {
  id: string;
  project_id: string;
  team_id: string;
  created_by: string;
  name: string;
  description?: string;
  display_code?: string;
  episode_id?: string | null;
  settings_json?: Record<string, unknown>;
  viewport_json?: { x: number; y: number; zoom: number };
  status: 'active' | 'archived' | 'deleted';
  /** Beats timeline target total runtime in seconds (M3); null/absent = unset. */
  target_duration_sec?: number | null;
  created_at: string;
  updated_at: string;
}

export interface ScriptChapter {
  id: string;
  script_id: string;
  parent_chapter_id?: string;
  chapter_number?: number;
  title?: string;
  summary?: string;
  content?: string;
  branch_label?: string;
  branch_type?: 'condition' | 'choice';
  position_x: number;
  position_y: number;
  width?: number;
  height?: number;
  data_json: Record<string, unknown>;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface ScriptProjectSummary {
  id: string;
  name: string;
  display_code?: string;
  status: string;
  created_at: string;
  updated_at: string;
  chapter_count?: number;
  // Present at runtime (episodes_router / script_projects.episode_id) but
  // was missing from this summary type — PR-10b workspace shell resolves
  // "this episode's script" by filtering on it (see ProjectWorkspace).
  episode_id?: string | null;
}

export type ProjectTab = 'files' | 'scripts' | 'storyboard' | 'output' | 'shares' | 'trash';

/** A single row from the global ``project_stages`` catalog (Phase 5b SOP). */
export interface ProjectStage {
  /** Snowflake BIGINT serialised as string for JS-safe transport. */
  id: string;
  slug: string;
  name: string;
  sort_order: number;
  /** Tool slugs recommended for this stage (advisory metadata from the catalog). */
  tools_recommended: string[];
  created_at?: string;
  updated_at?: string;
}

// ============================================
// Workflow templates + instances (Project Workflow M1, PR-C)
// All ids ride as strings (bigIntSafeFetch); owner is a single user XOR agent,
// members are a list of user-or-agent refs. Mirrors backend schemas/workflow.py.
// ============================================

/** A default (template) or live (instance) member — exactly one id is set. */
export interface WorkflowMemberRef {
  user_id?: string | null;
  agent_id?: string | null;
}

/** Who can mark a node complete (M2 PR-D Flow Rules tab). 'owner' (default)
 * = the node owner reviews & completes; 'any_editor' = any manager/editor. */
export type WorkflowCompletionPolicy = 'owner' | 'any_editor';

/** Per-node built-in event toggles (M2 PR-D Events tab). Arrival/completion
 * mirror-issue behavior stays hardcoded (spec §4) — these three booleans only
 * gate notifications + the suggest-agent-run chip. */
export interface WorkflowNodeEvents {
  notify_on_arrival: boolean;
  notify_on_complete: boolean;
  suggest_agent_run: boolean;
  /** M3 PR-H1/H3 (mig 389): arrival hook that pre-fills (never auto-launches)
   * an agent run — flips the suggest chip into a solid "Run now" button once
   * the hook has actually fired (see `ProjectStageNode.metadata.run_prepared_at`
   * below). Optional (not `?:` on the other two booleans above) so pre-mig-389
   * literals across the codebase keep compiling untouched; `normalizeEvents`
   * (workflowService.ts) still fills a real `false` once data flows through it. */
  prepare_agent_run?: boolean;
  /** Reserved for a future "chain into another workflow on completion" hook
   * (M3.5+) — never surfaced in the Events tab UI; always `null` today. */
  on_complete_workflow?: string | null;
  /** M4 Autopilot (mig 395, task O1/O2): template-layer config — when a node
   * with an agent owner arrives (deps satisfied) with no manual start, the
   * `autopilot_tick` engine begins it automatically (mirrors the manual
   * "Start early" action). Copied verbatim into the instance at
   * instantiation, then FROZEN there — not reachable from `ProjectNodePatch`
   * (no `events` field there at all), same idiom as `completion_policy`.
   * Optional (not `?:` on the first three booleans above) so pre-mig-395
   * literals across the codebase keep compiling untouched; `normalizeEvents`
   * (workflowService.ts) still fills a real `false` once data flows through it. */
  auto_start?: boolean;
}

/** Six-type whitelist for a node's deliverable form field (mig 390, M3 PR-I
 * §2). Mirrors backend `FormFieldType` (schemas/workflow.py). */
export type FormFieldType = 'text' | 'textarea' | 'number' | 'select' | 'checkbox' | 'date';

export const FORM_FIELD_TYPES: FormFieldType[] = [
  'text',
  'textarea',
  'number',
  'select',
  'checkbox',
  'date',
];

/** One field definition in a node's deliverable form (`form_schema`, mig 390).
 * `key` is server-generated (slugified from `label`, deduped within the node)
 * — the editor never invents or edits it; whatever rides in on a template
 * PATCH is harmlessly overwritten server-side. `options` only applies when
 * `type === 'select'` and must be non-empty there (backend 422s otherwise). */
export interface FormFieldDef {
  key: string;
  label: string;
  type: FormFieldType;
  required: boolean;
  options?: string[];
}

/** One node in a team workflow template (`workflow_template_nodes`). */
export interface WorkflowTemplateNode {
  id: string;
  template_id: string;
  name: string;
  sort_order: number;
  parallel_group: number | null;
  default_owner_user_id: string | null;
  default_owner_agent_id: string | null;
  skip_default: boolean;
  review_required: boolean;
  deliverable_required: boolean;
  deliverable_label: string | null;
  source_stage_id: string | null;
  duration_days: number | null;
  members: WorkflowMemberRef[];
  completion_policy: WorkflowCompletionPolicy;
  events: WorkflowNodeEvents;
  /** Deliverable form fields (mig 390, M3 PR-I) — template-layer config,
   * copied verbatim into `project_stage_nodes.form_schema` at instantiation
   * (spec §2; not open for in-place instance tweaks, same idiom as
   * `completion_policy`/`events`). */
  form_schema: FormFieldDef[];
  /** Dependency edges (mig 391, M3 PR-J) — REAL node ids on this GET response
   * (contrast `WorkflowTemplateNodeInput.depends_on`, which is a
   * payload-index contract on PATCH). Stable only until the *next* save —
   * see the editor's toDraft/toPayload for the id → position conversion. */
  depends_on: string[];
}

/** A template node as sent on PATCH (full node-list replacement). */
export interface WorkflowTemplateNodeInput {
  name: string;
  sort_order: number;
  parallel_group?: number | null;
  default_owner_user_id?: string | null;
  default_owner_agent_id?: string | null;
  skip_default?: boolean;
  review_required?: boolean;
  deliverable_required?: boolean;
  deliverable_label?: string | null;
  source_stage_id?: string | null;
  duration_days?: number | null;
  members?: WorkflowMemberRef[];
  completion_policy?: WorkflowCompletionPolicy;
  events?: WorkflowNodeEvents;
  form_schema?: FormFieldDef[];
  /** ⚠️ PAYLOAD-INDEX CONTRACT (mig 391, M3 PR-J): each string MUST be the
   * stringified 0-based position of another node within THIS SAME submitted
   * `nodes` array — never a node id (a template PATCH full-replaces every
   * node, so no id survives across two saves). Backend: `TemplateNodeIn.
   * depends_on` (backend/app/schemas/workflow.py). */
  depends_on?: string[];
}

/** A team workflow template list row (`node_count` on the collection). */
export interface WorkflowTemplate {
  id: string;
  team_id: string;
  name: string;
  is_default: boolean;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  node_count: number;
  /** Present on the detail (GET /{id}) payload only. */
  nodes?: WorkflowTemplateNode[];
}

/** One row of the read-only 11-node workflow node bank. */
export interface StageLibraryItem {
  id: string;
  slug: string;
  name: string;
  sort_order: number;
  phase: string;
  default_role_label: string | null;
  deliverable_label: string | null;
  review_required: boolean;
}

export type WorkflowNodeStatus =
  | 'pending'
  | 'in_progress'
  | 'in_review'
  | 'done'
  | 'skipped';

/** A live workflow node on a project (`project_stage_nodes`). */
export interface ProjectStageNode {
  id: string;
  project_id: string;
  source_template_node_id: string | null;
  legacy_stage_id: string | null;
  name: string;
  sort_order: number;
  parallel_group: number | null;
  status: WorkflowNodeStatus;
  owner_user_id: string | null;
  owner_agent_id: string | null;
  planned_start: string | null;
  planned_due: string | null;
  review_required: boolean;
  deliverable_required: boolean;
  deliverable_label: string | null;
  skipped: boolean;
  /** Creative surface this node maps to (B1, mig 402) — copied-frozen at
   * instantiation from the template. `'script' | 'storyboard' | 'renders'`
   * name a real creative face; `null` (or absent, on legacy pre-mig-402
   * instance rows) means a deliverable-only node (upload / version / review).
   * Optional like `form_schema?` so existing `ProjectStageNode` literals keep
   * compiling; `normalizeInstanceNode` fills a real value once data flows
   * through it, defaulting missing → `null` (the most conservative degrade,
   * spec §5③). Drives B5's episode view tabs + deliverable-node dashed border. */
  surface?: 'script' | 'storyboard' | 'renders' | null;
  // Deliverable folder link + filed-file count (M2-W1).
  folder_id?: string | null;
  deliverable_file_count?: number;
  members: WorkflowMemberRef[];
  completion_policy: WorkflowCompletionPolicy;
  events: WorkflowNodeEvents;
  /** Instance-node JSONB decoration (mig 389). Today the only key written is
   * `run_prepared_at` — an ISO timestamp the `stage_hook_dispatch` workflow
   * stamps when `events.prepare_agent_run` fires on arrival. Optional/tolerant
   * of a missing key entirely (pre-mig-389 rows, or a node the hook never
   * touched because it has no agent owner). */
  metadata?: { run_prepared_at?: string };
  /** Deliverable form fields (mig 390, M3 PR-I §2) — copied verbatim from the
   * template node at instantiation (I2); frozen on the instance (not
   * PATCH-able, see `ProjectNodePatch`). Optional (like `metadata` above) so
   * pre-mig-390 literals across the codebase keep compiling untouched;
   * `normalizeInstanceNode` (workflowService.ts) fills a real `[]` once data
   * flows through it. */
  form_schema?: FormFieldDef[];
  /** Live values entered into this node's deliverable form (Stage Board,
   * task I4) — instance-only, PATCH-able via `ProjectNodePatch.form_data`.
   * Unknown keys (not present in this node's own `form_schema`) are dropped
   * server-side on write. Optional/normalized the same way as `form_schema`. */
  form_data?: Record<string, unknown>;
  /** Dependency edges (mig 391, M3 PR-J) — REAL, stable instance node ids
   * (contrast the template-side payload-index contract) this node depends
   * on. Optional/normalized the same way as `form_schema`/`form_data` so a
   * pre-mig-391 row never hands `undefined` to a consumer. */
  depends_on?: string[];
  /** Pre-work notes for whoever works this stage (mig 395, M4 Autopilot task
   * O1/O2) — instance-only runtime text, writable any time before the node
   * finishes (PATCH-able, see `ProjectNodePatch.brief`). Injected into an
   * agent's task context on auto-start/dispatch/start-early alike; surfaced
   * read-only once the node reaches `in_review` (pinned in the Stage Board's
   * review context + the mirror issue) and permanently read-only once
   * `done`/`skipped`. Optional/normalized the same way as `form_schema` above
   * so a pre-mig-395 row never hands `undefined` to a consumer. */
  brief?: string;
}

/** GET /projects/{id}/workflow payload. */
export interface ProjectWorkflow {
  has_workflow: boolean;
  current_node_id: string | null;
  agents_active: number;
  nodes: ProjectStageNode[];
}

/** Minimal read-only issue reference used by the Stage Board (mirror issue /
 * sub-issue row) — a slice of the full `Issue` shape (services/issuesService.ts),
 * only what `GET .../board` actually projects. */
export interface StageBoardIssueRef {
  id: string;
  identifier: string | null;
  title: string | null;
  status: string;
  assignee: { user_id: string | null; agent_id: string | null };
}

/** The node's mirror issue, with its sub-issues inlined (Stage Board F1). */
export interface StageBoardIssue extends StageBoardIssueRef {
  sub_issues: StageBoardIssueRef[];
}

/** One file filed into the node's deliverable folder (Stage Board F1). */
export interface StageBoardFile {
  id: string;
  filename: string | null;
  size: number | null;
  created_at: string | null;
  /** Identifier of the issue the file was uploaded from, when known. */
  source_issue_identifier: string | null;
}

/**
 * `GET /projects/{id}/workflow/nodes/{node_id}/board` payload (M2 PR-F F1) —
 * the Stage Board workspace module's single data source: the node's full row,
 * its mirror issue (or `null` — no mirror yet, or a legacy project whose
 * mirror predates the current origin-id format), and the files filed into its
 * deliverable folder.
 */
export interface StageBoardData {
  node: ProjectStageNode;
  issue: StageBoardIssue | null;
  files: StageBoardFile[];
}

/** POST body to add a node to a live instance (M2-W3-1). Exactly one of
 * `source_stage_id` (from the node bank) or `name` (blank). */
export interface ProjectNodeCreate {
  source_stage_id?: string | null;
  name?: string | null;
  sort_order: number;
  parallel_group?: number | null;
}

/** PATCH body for an in-place node tweak. */
export interface ProjectNodePatch {
  owner_user_id?: string | null;
  owner_agent_id?: string | null;
  members?: WorkflowMemberRef[];
  planned_start?: string | null;
  planned_due?: string | null;
  skipped?: boolean;
  /** Live values entered into this node's deliverable form (mig 390, M3 PR-I
   * §2, task I4). Deliberately NOT paired with a `form_schema` field here —
   * the form's field definitions are template-layer config, frozen at
   * instantiation; an instance may fill in data but may not change what
   * fields exist. Server merges this with the existing `form_data`
   * (whitelisted to this node's own `form_schema` keys), so callers only
   * need to send the field(s) that actually changed. */
  form_data?: Record<string, unknown>;
  /** Dependency edges (mig 391, M3 PR-J) — full-replace, real instance node
   * ids (this node's live project siblings). Instance structure (like
   * members), not template config — unlike `form_schema` this may be edited
   * on the instance directly. Backward-only (target sort_order strictly
   * smaller) is validated server-side. */
  depends_on?: string[];
  /** Pre-work notes (mig 395, M4 Autopilot task O1/O2) — full-replace instance
   * text. Server merges are unnecessary here (unlike `form_data`'s per-key
   * whitelist merge) since this is a single free-text field, not a keyed map. */
  brief?: string;
}

/** Blocked-reason codes the advance predicate can return (spec §7). */
export type AdvanceBlockedReason =
  | 'NOT_MANAGER_OR_EDITOR'
  | 'REVIEW_PENDING'
  | 'DELIVERABLE_MISSING'
  | 'FORM_INCOMPLETE'
  | 'NO_NEXT'
  | 'DEPS_PENDING';

/** A node named in an advance preview (closing / creating list). */
export interface AdvanceNodeRef {
  node_id: string;
  name: string;
  assignee_user_id: string | null;
  assignee_agent_id: string | null;
  due_date: string | null;
}

/** Server-computed advance ruling (shared by preview + execute, #1400). */
export interface AdvancePreview {
  direction: 'forward' | 'back';
  will_advance: boolean;
  blocked_reason: AdvanceBlockedReason | null;
  closing: AdvanceNodeRef[];
  creating: AdvanceNodeRef[];
  warnings: string[];
  /** Required form-field LABELS (never keys) missing from the ACTIVE
   * (current) group when `blocked_reason === 'FORM_INCOMPLETE'` (mig 390,
   * M3 PR-I §2) — Gate 3 checks the group that's about to close, not the
   * target group Gate 5 (deps) checks. Empty for every other ruling. */
  missing_fields: string[];
  /** Names of target-group nodes whose dependencies aren't yet done/skipped
   * when `blocked_reason === 'DEPS_PENDING'` (mig 391, M3 PR-J). Server-ruled
   * — the confirm dialog renders this verbatim, never a local derivation.
   * Empty for every other ruling. */
  waiting_on: string[];
}

/**
 * Per-episode progress row from `GET /api/v1/projects/{id}/episodes/progress`
 * (PR-10b workspace shell, spec G12) — script/scene/shot counts plus a
 * server-derived pipeline status. `status` is one of
 * planned/drafting/boarding/boarded/rendered (see episode_repository.py
 * `_derive_episode_status`); kept as `string` here so the frontend degrades
 * gracefully instead of throwing on a future status value.
 */
export interface EpisodeProgress {
  episode_id: string;
  title: string;
  sort_order: number;
  /** 集负责人 (Task 6, mig — Episodes.owner_id). null = unassigned. Drives
   * Task 9's `canEditConfig` (project owner OR this episode's owner may
   * edit that episode's node config in place). */
  owner_id?: string | null;
  script_count: number;
  scene_count: number;
  shots_total: number;
  shots_done: number;
  renders_count: number;
  status: string;
  /** B4 真数据(2026-08-08):每集工作流节点计数与 agent 提问数。optional —
   * fetchEpisodesProgress 原样透传,旧后端/测试桩缺省时消费方回退旧近似
   * (进度条回落 status 五段阶梯、"等你回答"回落 planned 计数)。 */
  workflow?: EpisodeWorkflowRollup;
  /** B4 判据当前值(派生,可与节点 status 合法不一致=产物被删的信号)。 */
  surface_state?: EpisodeSurfaceState;
}

/** 每集工作流汇总(episode_repository.progress_by_project 的 workflow 键)。 */
export interface EpisodeWorkflowRollup {
  nodes_total: number;
  nodes_done: number;
  /** BIGINT 走字符串防精度丢失;无游标时为 null。 */
  current_node_id: string | null;
  needs_input_count: number;
}

/** surface 完成判据的当前派生值(script/storyboard 两档, spec §5)。 */
export interface EpisodeSurfaceState {
  script: boolean;
  storyboard: boolean;
}

/**
 * Project-level ASSETS "main library" rows from
 * `GET /api/v1/projects/{id}/entities` (PR-10b, spec G13) — characters and
 * locations are derived from script cues/scene headers, never hand-authored,
 * so there's no separate write path here.
 */
export interface ProjectEntityCharacter {
  name: string;
  cue_count: number;
  episode_ids: string[];
}

export interface ProjectEntityLocation {
  name: string;
  scene_count: number;
  episode_ids: string[];
}

export interface ProjectEntities {
  characters: ProjectEntityCharacter[];
  locations: ProjectEntityLocation[];
}

/**
 * A single `generated_media` row from `GET /api/v1/projects/{id}/renders`
 * (PR-10b, spec G12 Renders module) — image frames and shot videos produced
 * for the project's episodes. No `cover`/`stream` URL field on the row
 * itself; the frontend builds `/api/v1/generated-media/{id}/cover` (image)
 * or `/api/v1/generated-media/{id}/stream` (video) from `id` + `media_kind`.
 */
export interface RenderItem {
  id: string;
  media_kind: 'image' | 'video' | string;
  mime: string | null;
  origin_kind: string;
  node_id: string | null;
  created_at: string;
}

export interface RenderItemPage {
  items: RenderItem[];
  next_cursor: string | null;
}

/** Aggregate shot-frame progress for the storyboard stage suggestion (Phase B B3). */
export interface StoryboardProgress {
  total: number;
  done: number;
  empty: number;
  generating: number;
  failed: number;
  script_count: number;
  scene_count: number;
}

/** The action a work-queue suggestion CTA performs: fire the one-click batch, or navigate a tab. */
export interface SuggestionAction {
  type: 'generate_missing_frames' | 'navigate';
  label_key: string;
  tab?: ProjectTab | null;
  count?: number | null;
}

/**
 * One row of the batch "what's next" feed for the homepage work queue
 * (PR-9, G7) — `GET /api/v1/projects/suggestions`. Carries the per-project
 * `kind`/`progress`/`action` next-step hint plus the project identity + stall
 * flag + latest activity needed to render a queue row without a second
 * per-project fetch.
 */
export interface ProjectSuggestionItem {
  project_id: string;
  name: string;
  stage_slug: string | null;
  kind: string;
  progress?: StoryboardProgress | null;
  action?: SuggestionAction | null;
  stalled: boolean;
  latest_activity?: ProjectCardActivity | null;
}

/** One recently-edited script or canvas for the Projects "Recent" view. */
export interface RecentItem {
  kind: 'script' | 'canvas';
  id: string;
  name: string;
  project_id: string;
  project_name: string;
  // The item's OWN project's team (null for a personal project with no
  // team) — the Recent view is owner-scoped across every team the caller
  // belongs to, so navigation MUST use this field, never the current page's
  // teamId (see ProjectsPage.handleRecentSelect).
  team_id: string | null;
  updated_at: string | null;
}

export type ScriptAssetType = 'worldview' | 'character' | 'location' | 'prop' | 'plot_point';

export interface ScriptAsset {
  id: string;
  script_id: string;
  asset_type: ScriptAssetType;
  name: string;
  content?: string;
  data_json: Record<string, unknown>;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface Skill {
  id: string;
  team_id?: string;
  project_id?: string;
  name: string;
  description?: string;
  content_md?: string;
  category?: string;
  icon: string;
  output_format?: string;
  trigger_keywords: string[];
  is_public: boolean;
  status: string;
  created_by?: string;
  created_at: string;
  updated_at: string;
}

/** @deprecated Use Skill instead */
export type StyleTemplate = Skill;

export interface StoryboardNode {
  id: string;
  project_id: string;
  node_type: 'upload' | 'image' | 'image_edit' | 'storyboard_split' | 'storyboard_gen' | 'text_annotation' | 'group' | 'export' | 'image_to_video';
  position_x: number;
  position_y: number;
  width?: number;
  height?: number;
  data_json: Record<string, unknown>;
  sort_order: number;
  locked: boolean;
  created_at: string;
  updated_at: string;
}

export interface StoryboardEdge {
  id: string;
  project_id: string;
  source_node_id: string;
  target_node_id: string;
  source_handle?: string;
  target_handle?: string;
  edge_type: string;
  created_at: string;
}

export interface StoryboardFrame {
  id: string;
  node_id: string;
  project_id: string;
  frame_index: number;
  image_url?: string;
  thumbnail_url?: string;
  note?: string;
  shot_type?: string;
  camera_angle?: string;
  camera_movement?: string;
  focal_length?: string;
  lighting?: string;
  duration_seconds: number;
  transition_type: 'cut' | 'fade' | 'dissolve';
  annotations_json?: Record<string, unknown>;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface StoryboardCharacter {
  id: string;
  project_id: string;
  name: string;
  description?: string;
  reference_image_url?: string;
  thumbnail_url?: string;
  visual_traits?: Record<string, string>;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary {
  id: string;
  name: string;
  cover_image_url?: string;
  status: string;
  created_at: string;
  updated_at: string;
  frame_count?: number;
  character_count?: number;
}

// ---------- AI Library ----------
//
// Types for the AI Library feature (prompts/skills library, backend route
// `/api/v1/ai-library/*`).
//
// Naming: prefixed `AILibrary*` to avoid collision with:
//   - legacy `AIAgent` in `services/aiService.ts` (chat agent — different shape)
//   - existing top-level `Skill` interface above (style templates — different shape)
//
// BIGINT precision note: backend returns `skills.id` as JSON number. For Phase 1
// these are small serial-like values well below 2^53, so `number` is safe.
// `ai_agents.id` and `skill_files.id` are UUID strings.

export interface AgentChatPermissions {
  enabled?: boolean;
  read_team_resources?: boolean;
  auto_broadcast?: boolean;
  allowed_team_ids?: number[];
}

/** Write-level tiers for high-risk grants. `propose` produces changes a human
 *  accepts; it does not write to storage. `none` is the fail-closed default —
 *  even read-tier screenwriting tools must be granted explicitly. */
export type AgentWriteLevel = 'none' | 'read' | 'propose' | 'write';

export interface AgentMediaCaps {
  image?: boolean;
  video?: boolean;
  /** Per-turn ceiling on paid generations. Backend clamps to 0..100. */
  max_calls_per_turn?: number;
}

/**
 * High-risk capability grants (spec §2), stored under
 * `capability_profile.capabilities` and enforced fail-closed by the backend.
 *
 * Used for BOTH directions: as a partial PATCH body (omit a field to leave it
 * unchanged) and as the resolved read projection on `AILibraryAgent`, where the
 * API always fills every field. Send `false` / `'none'` to revoke — omitting a
 * field never revokes anything.
 */
export interface AgentCapabilities {
  write_level?: AgentWriteLevel;
  delete?: boolean;
  media?: AgentMediaCaps;
  cross_episode_read?: boolean;
  external_publish?: boolean;
}

export interface AILibraryAgent {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  /** Lucide icon slug (e.g. 'bot', 'sparkles'). Null → UI falls back to default. */
  icon?: string | null;
  model: string;
  temperature: number;
  max_tokens: number;
  identity_md?: string | null;
  soul_md?: string | null;
  agent_md?: string | null;
  /** Roster group for the gallery (mig 400). Null → bucketed under 'tools'. */
  agent_group?: string | null;
  is_system_preset: boolean;
  team_id?: number | null;
  project_id?: number | null;
  user_id?: string | null;
  enabled: boolean;
  skill_ids: number[]; // BIGINT from backend — Phase 1 values fit in Number
  created_at: string;
  updated_at: string;
  // Phase 2 PR 2.9 — denormalized by the backend for scope badges.
  team_name?: string | null;
  project_name?: string | null;
  // Budget-guard columns (migration 148). Null budget = unlimited.
  monthly_token_budget?: number | null;
  /** mig 286 run limits — null = unlimited. */
  timeout_sec?: number | null;
  max_concurrent_runs?: number | null;
  monthly_cost_cents_budget?: number | null;
  /** 'budget' when sweeper detects over-spend, 'manual' when admin pauses. */
  paused_reason?: 'budget' | 'manual' | null;
  // Team Chat PHASE-0: resolved chat caps, served by AgentOut.chat_permissions
  // (review C1 — capability_profile itself is NOT exposed on the wire).
  // Always present from the API (defaults all-false); optional here for forward-compat.
  chat_permissions?: AgentChatPermissions;
  // A8: resolved (fail-closed) high-risk grants, served by AgentOut.capabilities.
  // Always present from the API (defaults all-denied); optional here for
  // forward-compat with responses predating the field.
  capabilities?: AgentCapabilities;
  // Agent-overrides (mig 341). Single-get: which layer produced this merged
  // view + the fields it replaced. List: override_scopes marks presets the
  // caller (or their teams) customized — drives the sidebar badge.
  override_scope?: 'user' | 'team' | null;
  override_fields?: string[];
  override_scopes?: string[];
}

/**
 * One row of an agent's permission-change audit trail (2026-08-10 spec §3,
 * `GET /agents/{slug}/permission-audits`, read-only). `before`/`after` are
 * RESOLVED (fail-closed) snapshots of the `chat` + `capabilities` subtrees —
 * the same shape `chat_permissions`/`capabilities` are served in, nested
 * under those two keys — not the raw JSONB storage.
 */
export interface AgentPermissionAudit {
  id: string;
  /** Supabase auth user id (UUID string) — display truncated, never parseInt. */
  changed_by: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  reason: string | null;
  created_at: string;
}

/**
 * Payload for POST /agents — creating a new user-owned (non-preset) agent.
 * `fork_from` optionally copies identity_md / soul_md / agent_md / model /
 * temperature / max_tokens from an existing agent. Explicit field overrides
 * in the payload win over forked values.
 */
export interface CreateAgentPayload {
  slug: string;
  name: string;
  description?: string;
  fork_from?: string;
  // Scope (Phase 2 PR 2.9) — at most one of team_id / project_id. Omit both
  // for a private per-user agent.
  // Snowflake BIGINTs — string preserves precision past 2^53.
  team_id?: number | string;
  project_id?: number | string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  identity_md?: string;
  soul_md?: string;
  agent_md?: string;
}

export interface AILibrarySkillFile {
  id: string;
  skill_id: number; // BIGINT
  path: string;
  content?: string | null;
  file_type: 'markdown' | 'script' | 'text-asset' | 'binary-ref';
  binary_url?: string | null;
  updated_at: string;
}

export interface AILibrarySkillAgentRef {
  slug: string;
  name: string;
}

export interface AILibrarySkill {
  id: number; // BIGINT
  slug?: string;
  name: string;
  description?: string | null;
  body_md?: string | null;
  category?: string | null;
  icon?: string | null;
  is_public: boolean;
  team_id?: number | null;
  project_id?: number | null;
  output_format?: string | null;
  frontmatter_json: Record<string, unknown>;
  files: AILibrarySkillFile[];
  // Reverse index: agents that bind this skill. Populated by BOTH the
  // detail and the list endpoint (the list batches it into one JOIN).
  // Optional here for forward-compat with older payloads — treat
  // undefined and [] alike: both mean nothing binds this skill.
  agents?: AILibrarySkillAgentRef[];
  updated_at: string;
  // Phase 2 minor cleanup — denormalized by the backend for scope badges.
  team_name?: string | null;
  project_name?: string | null;
}

/**
 * Payload for POST /skills — creating a new user-owned (non-preset) skill.
 * ``fork_from`` optionally copies body_md / frontmatter_json / category /
 * icon / output_format / description from an existing skill. Explicit fields
 * in the payload win over forked values. ``team_id`` and ``project_id`` are
 * mutually exclusive — omit both for a private per-user skill.
 */
export interface CreateSkillPayload {
  slug: string;
  name: string;
  description?: string;
  category?: string;
  icon?: string;
  body_md?: string;
  frontmatter_json?: Record<string, unknown>;
  output_format?: string;
  // Snowflake BIGINTs — string preserves precision past 2^53.
  team_id?: number | string;
  project_id?: number | string;
  fork_from?: string;
}

// ─── Agent Runs (telemetry) ────────────────────────────────────────────────
// Mirrors backend/app/schemas/agent_runs.py. Written by RunRecorder on every
// agent invocation; read by the AgentEditor "Runs" sub-tab and the
// Settings → AI Usage dashboard.

export type AgentRunStatus =
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'heartbeat_lost';

/** Slim row for the Runs list view — no metadata_json / full_output. */
export interface AgentRunListItem {
  id: string; // UUID
  agent_id: string; // UUID
  status: AgentRunStatus;
  trigger: string;
  model?: string | null;
  provider?: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  /** Fractional cents — snapshot-priced at run start so history is immutable. */
  cost_cents?: number | null;
  started_at: string; // ISO timestamp
  ended_at?: string | null;
  error_code?: string | null;
  skill_slugs_used: string[];
  /** task_tracking PK when the run executed inside a tracked workflow (mig 282). */
  task_id?: string | null;
  /** 500-char display summary — left-column snippet in the split-pane Runs tab. */
  output_summary?: string | null;
  /** conversations.id (numeric-string Snowflake, mig 331) for chat turns; null otherwise. */
  conversation_id?: string | null;
}

/** One row of the conversation-grouped Runs view: a whole chat conversation
 * (run_count > 1 possible) or a single non-chat run. */
export interface AgentRunGroupItem {
  group_key: string;
  conversation_id?: string | null;
  /**
   * Display name projected by the backend: conversation title → issue title →
   * first user message (60-char cap). Null for pipeline runs that name no
   * conversation — `conversationTitle()` supplies the translated fallback.
   * Mirrors RunGroupItem.title in backend/app/schemas/agent_runs.py.
   */
  title?: string | null;
  run_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_cents?: number | null;
  first_started_at: string;
  last_started_at: string;
  any_running: boolean;
  error_count: number;
  latest_run_id: string;
  latest_status: AgentRunStatus;
  trigger: string;
  model?: string | null;
  latest_output_summary?: string | null;
  latest_error_code?: string | null;
  latest_ended_at?: string | null;
}

export interface AgentRunGroupListResponse {
  items: AgentRunGroupItem[];
  total: number;
  limit: number;
  offset: number;
}

/** Slim task_tracking ref attached to a run detail ("Tasks Touched"). */
export interface AgentRunTaskRef {
  id: string;
  title?: string | null;
  phase?: string | null;
  task_type?: string | null;
}

/** One currently-running run in the Workforce live strip. */
export interface LiveAgentRun {
  id: string;
  agent_id: string;
  status: 'running';
  trigger: string;
  model?: string | null;
  started_at: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_cents?: number | null;
  input_summary?: string | null;
  task_id?: string | null;
  agent_slug?: string | null;
  agent_name?: string | null;
  agent_icon?: string | null;
}

/** One transcript event of a run (mig 285 agent_run_events). */
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

export interface AgentRunEvent {
  seq: number;
  // 'capability_denied': Task 5 (Agent 权限页梳理立项) — the capability gate's
  // abort, recorded once per tool per turn (backend/app/services/ai/runner/
  // agent_runner.py). payload: { tool: string, reason: string }.
  event_type: AgentRunEventType;
  payload: Record<string, unknown>;
  created_at: string;
  /** mig 453 step coordinates; null on rows written before them. */
  turn?: number | null;
  step?: number | null;
}

/** Full detail view — adds summaries, metadata, snapshots, and cancel state. */
export interface AgentRunDetail extends AgentRunListItem {
  session_id?: string | null;
  team_id?: number | null;
  project_id?: number | null;
  heartbeat_at: string;
  cancel_requested: boolean;
  prompt_cents_per_1k_snapshot?: number | null;
  completion_cents_per_1k_snapshot?: number | null;
  cached_input_tokens?: number;
  input_summary?: string | null;
  error_message?: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  /** Resolved from task_id by the backend (null when not workflow-linked). */
  task?: AgentRunTaskRef | null;
  /** Set once this run's writes have been undone (one-shot; see AgentRunUndoReport). */
  undone_at?: string | null;
}

/** Response of POST /runs/{run_id}/undo — what got reverted, and what didn't. */
export interface AgentRunUndoReport {
  status: 'done' | 'already_undone';
  shots_deleted: number;
  shots_reverted: number;
  scene_elements_reverted: number;
  skipped: {
    kind: 'shot' | 'scene' | 'scene_element';
    id: string;
    reason: 'edited_after_run' | 'rendered' | 'version_conflict' | 'internal_error';
  }[];
}

export interface AgentRunListResponse {
  items: AgentRunListItem[];
  total: number;
  limit: number;
  offset: number;
}

// ─── Per-agent Dashboard aggregate ─────────────────────────────────────────
// Backed by GET /api/v1/ai-library/agents/{slug}/dashboard. Paperclip-style
// 14-day overview, scoped to the authenticated user.

export interface DailyCount {
  date: string; // YYYY-MM-DD
  count: number;
}

export interface DailySuccessRate {
  date: string;
  success: number;
  total: number;
}

export interface DashboardCosts {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost_cents: number;
  run_count: number;
}

export interface DashboardRecentTask {
  id: string;
  lifecycle_status: string;
  created_at: string;
  started_at?: string | null;
  ended_at?: string | null;
  title?: string | null;
  error_code?: string | null;
  error_message?: string | null;
}

export interface DashboardRecentRun {
  id: string;
  status: string;
  trigger: string;
  model?: string | null;
  started_at: string;
  ended_at?: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_cents?: number | null;
}

/** GET /ai-library/agents/{slug}/usage — "Used by" card data. */
export interface AgentUsageModuleRef {
  /** Team-scoped route segment (resources / projects / canvas / parser / issues). */
  module_key: string;
  /** i18n sub-label key: aiLibrary.agents.usage.feature.<feature_key>. */
  feature_key: string;
}

export interface AgentUsage {
  modules: AgentUsageModuleRef[];
  trigger_counts: { trigger: string; feature_key: string; count: number }[];
  conversation_count: number;
  routine_count: number;
  window_days: number;
}

export interface AgentDashboard {
  agent: {
    id: string;
    slug: string;
    name: string;
    icon?: string | null;
    model?: string | null;
    persistent: boolean;
    paused_reason?: string | null;
  };
  /** Most recent run, regardless of window. Null when never invoked. */
  latest_run: AgentRunDetail | null;
  /** 14 entries, oldest first; gaps filled with count=0. */
  run_activity_14d: DailyCount[];
  /** lifecycle_status → count, last 14d. May omit zero buckets. */
  tasks_by_status_14d: Record<string, number>;
  /** 14 entries with daily success/total counts. */
  success_rate_14d: DailySuccessRate[];
  /** Sums + total cost over the 14-day window. */
  costs_14d: DashboardCosts;
  recent_tasks: DashboardRecentTask[];
  recent_runs: DashboardRecentRun[];
}

// ─── AI Usage aggregates ───────────────────────────────────────────────────
// Mirror of backend/app/schemas/agent_runs.py (UsagePerAgent, UsageAggregate).
// Fed by GET /api/v1/ai-library/usage?scope=&month=[&team_id=&project_id=].

export type UsageScope = 'user' | 'team' | 'project';

export interface UsagePerAgent {
  agent_id: string; // UUID
  agent_slug?: string | null;
  agent_name?: string | null;
  run_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  /** Fractional cents — same convention as agent_runs.cost_cents. */
  cost_cents: number;
  failed_count: number;
}

export interface UsageAggregate {
  scope: UsageScope;
  /** YYYY-MM (calendar month, aggregated in UTC). */
  month: string;
  total_runs: number;
  total_tokens: number;
  total_cost_cents: number;
  per_agent: UsagePerAgent[];
}

// Row-level usage detail — GET /api/v1/ai-library/usage/runs (caller-scoped).
export interface UsageRunItem {
  id: string;
  agent_id: string | null;
  agent_slug?: string | null;
  agent_name?: string | null;
  model: string | null;
  provider: string | null;
  status: string;
  trigger: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  /** Fractional cents — same convention as agent_runs.cost_cents. */
  cost_cents: number;
  duration_ms: number | null;
  started_at: string | null;
  error_code: string | null;
}

export interface UsageRunsPage {
  items: UsageRunItem[];
  total: number;
  page: number;
  page_size: number;
}

// Daily rollup grouped by model or agent — GET /api/v1/ai-library/usage/daily.
export interface UsageDailyRow {
  date: string;
  /** Group key: model name, or agent uuid when group_by=agent. */
  key: string | null;
  /** Display label (agent name resolved server-side; = key for models). */
  label: string | null;
  requests: number;
  failed_requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_cents: number;
}

export type UsageGroupBy = 'model' | 'agent';

export interface UsageDailySummary {
  days: number;
  /** Set when the window is a calendar month (YYYY-MM) instead of last-N-days. */
  month: string | null;
  group_by: UsageGroupBy;
  total_requests: number;
  total_failed: number;
  total_tokens: number;
  total_cost_cents: number;
  daily: UsageDailyRow[];
}

// ─── AI Library chat ────────────────────────────────────────────────────────
// Mirror of backend/app/schemas/ai_library_chat.py. One session = one agent
// binding; messages + run telemetry flow through AgentRunner + RunRecorder.

export type ChatMessageRole = 'user' | 'assistant' | 'system';

export interface AIChatMessageAttachment {
  kind: string; // image | video | pdf | resource_ref | asset_ref
  resource_id?: string | null;
  /** P5: `assets.id` for `kind === 'asset_ref'`. Persisted server-side —
   *  `_DISPLAY_ATTACHMENT_KEYS` in `conversations_ai_store.py` carries it —
   *  so a reloaded bubble can still name the asset it was about. */
  asset_id?: string | null;
  // No `loadout_id` here on purpose: the server's `_DISPLAY_ATTACHMENT_KEYS`
  // whitelists it, but the reducer drops nulls and v1 always sends null, so no
  // persisted row can carry one. Declaring it would invite a read that is
  // always undefined (final review M2). Add it back with the v2 loadout
  // picker, which is when a value first exists.
  mime?: string | null;
  alt_text?: string | null;
  name?: string | null;
  /** Client-side only: local preview for the optimistic bubble (not persisted). */
  preview_data_url?: string;
}

export interface AIChatMessage {
  id: string; // UUID
  session_id: string;
  role: ChatMessageRole;
  content: string;
  agent_id?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  metadata_json?: Record<string, unknown> | null;
  attachments?: AIChatMessageAttachment[] | null;
  created_at?: string | null;
}

export interface ChatSession {
  id: string; // UUID
  user_id: string;
  agent_id?: string | null;
  agent_slug?: string | null;
  team_id?: number | null;
  project_id?: number | null;
  title?: string | null;
  context_type?: string | null;
  context_id?: string | null;
  status?: string | null;
  total_tokens?: number | null;
  message_count?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ChatSessionWithMessages extends ChatSession {
  messages: AIChatMessage[];
}

export interface CreateChatSessionPayload {
  title?: string;
  // Snowflake BIGINTs — string preserves precision past 2^53.
  project_id?: number | string;
  team_id?: number | string;
  context_type?: string;
  context_id?: string;
}

/** Phase 3: per-user token usage summary (driven by ai_usage_logs). */
export interface AILibraryUsageSummary {
  window_start: string;
  window_end: string;
  overall: {
    total_tokens: number;
    cost_points: number;
    run_count: number;
  };
  by_model: Array<{
    model: string;
    total_tokens: number;
    cost_points: number;
    run_count: number;
  }>;
  by_day: Array<{
    date: string;
    total_tokens: number;
    cost_points: number;
    run_count: number;
  }>;
}

/** Phase 3: AI Library version history item (agent / skill / skill_file).
 *  List view shape — only metadata. Full body via the detail endpoint. */
export interface AILibraryVersionItem {
  id: string;
  version_number: number;
  notes: string | null;
  created_by: string | null;
  created_at: string | null;
  /** agent-only */
  model?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  /** skill_file-only */
  path?: string;
  file_type?: string;
}

/** G1: Approval request row from agent_approval_requests (mig 198).
 *  Surfaced to the user when an agent hook returns await_approval.
 *  All times ISO strings. payload is hook-defined JSON. */
export interface AILibraryApprovalRequest {
  id: string;
  agent_id: string;
  session_id: string | null;
  run_id: string | null;
  hook_name: string;
  reason: string;
  payload: Record<string, unknown>;
  created_at: string | null;
  expires_at: string | null;
}

/** A: AI Library MCP server registration row.
 *  bearer_token is never returned — only has_bearer_token boolean. */
export interface AILibraryMCPServer {
  id: string;
  name: string;
  url: string;
  description: string | null;
  enabled: boolean;
  has_bearer_token?: boolean;
}

/** O5: AI Library memory row, as returned by GET /memories. */
export interface AILibraryMemory {
  id: string;
  agent_id: string;
  user_id: string | null;
  scope: string;
  summary: string;
  when_to_use: string | null;
  status: 'active' | 'archived' | 'superseded';
  kind: 'declarative' | 'procedural' | 'episodic' | null;
  thread_id: string | null;
  session_id: string | null;
  extracted_from: string | null;
  reinforce_count: number;
  last_reinforced_at: string | null;
  decay_score: number | null;
  created_at: string | null;
  updated_at: string | null;
}

/** O4: AI Library commitment (followup) row, as returned by GET /commitments. */
export interface AILibraryCommitment {
  id: number;
  agent_id: string;
  session_id: string | null;
  description: string;
  trigger_type: 'time' | 'event' | 'next_session' | null;
  trigger_at: string | null;
  trigger_event: string | null;
  status: 'pending' | 'fulfilled' | 'cancelled' | 'failed' | 'expired';
  created_at: string | null;
  fulfilled_at: string | null;
  expires_at: string | null;
}

/**
 * One Skill / Delegate dispatch the LLM made during a chat turn.
 * Mirrors backend ``ChatToolCall``. Returned at the top level of
 * ``ChatResponse`` (live turn) AND folded into the assistant message's
 * ``metadata_json.tool_calls`` so refetched history keeps it.
 */
export interface ChatToolCall {
  /** 'Skill' | 'Delegate' (open string in case the runner adds more). */
  name: string;
  /** 1-indexed loop tick within the turn. */
  iteration: number;
  /** Raw arg payload the agent runner saw. */
  args: Record<string, unknown>;
  /** Raw result payload the dispatched tool returned. */
  result: Record<string, unknown>;
}

/**
 * One attachment the backend could not resolve for a chat turn (G2).
 * Mirrors backend ``AttachmentFailure`` — ``index`` is 0-based into the
 * binary (non-``resource_ref``) attachments sent with that turn.
 */
export interface ChatAttachmentFailure {
  index: number;
  kind: string;
  reason: string;
}

export interface ChatResponse {
  message: AIChatMessage;
  usage: { prompt_tokens?: number; completion_tokens?: number };
  run_id?: string | null;
  tool_calls?: ChatToolCall[];
  /** Empty/absent on success — non-empty means some attachments degraded
   *  to text-only. Surfaced to the user via AttachmentFailureBanner. */
  attachment_failures?: ChatAttachmentFailure[];
}

/** Reference attachment for chat composer @-mention.
 *  Body shape mirrors the backend `resource_ref` resolver expectation.
 */
export type ResourceRefAttachment = {
  kind: 'resource_ref';
  resource_id: string;        // BIGINT serialized as string (Snowflake)
  name: string;               // snapshot — UI uses this even if resource deleted later
  mime: string;
  scope: { type: 'personal' | 'team'; id: string };
};

/** P5 reference attachment for a library ASSET (asset sheet / asset card
 *  "Send To Agent", and — from Task 6 — the @ picker's Assets tab).
 *
 *  Mirrors `AttachmentRequest`'s asset fields exactly. `mime` and `url` are
 *  present and EMPTY on purpose rather than omitted — but NOT because the
 *  backend reads them: an `asset_ref` never enters the binary bucket at all
 *  (`ai_library_chat_service` splits it off by kind before
 *  `chat_attachment_resolver` sees anything), so nothing on the server reads
 *  either field. The real reason is shape parity for a human: every
 *  attachment on this wire carries the same six keys, so a payload in a log or
 *  a network tab reads the same way whichever kind it is, and `''` states
 *  "nothing to claim" where `undefined` would read as "forgot to set".
 *  Consequence worth knowing: `mime: ''` is not null, so the persist reducer
 *  keeps it and a reloaded bubble carries an empty mime nothing renders.
 */
export type AssetRefAttachment = {
  kind: 'asset_ref';
  /** BIGINT serialized as string (Snowflake). */
  asset_id: string;
  /** `asset_loadouts.id`, or null for "use the default loadout".
   *  Both values are live as of v2: staging an asset from either entry point
   *  (the asset sheet's Send To Agent, the @ picker's Assets tab) still sends
   *  null, and the staged chip's loadout menu can then replace it with a real
   *  id. Null keeps its MEANING — the server's default-loadout behaviour is
   *  defined against it (ruling D) — so it is never a missing value.
   *  A loadout the asset does not own is refused as a typed
   *  `loadout_not_owned` failure and the reference is dropped; the server no
   *  longer falls back to the default. */
  loadout_id: string | null;
  /** Snapshot — the bubble uses this even if the asset is renamed later. */
  name: string;
  mime: '';
  url: '';
};

/** AI processing state of a resource (`resources.transcript_status`
 *  etc.). `skipped` means the backend decided there is nothing to
 *  process — e.g. a video with no audio track. */
export type ResourceProcessingStatus =
  | 'none'
  | 'pending'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'skipped';

/** Search result row from GET /api/v1/resources/search */
export type ResourceSearchResult = {
  id: string;
  name: string;
  kind: 'video' | 'image' | 'doc' | 'audio' | 'pdf';
  mime: string | null;
  size: number | null;
  scope: { type: 'personal' | 'team'; id: string };
  updated_at: string;
  /** Relative path (`/api/v1/resources/{id}/cover`) or null when the
   *  resource has no cover — prefix with the API base before use. */
  thumbnail_url: string | null;
  /** AI processing state mirrored from `resources`. Optional because
   *  older callers build this shape by hand; the API always sends both. */
  transcript_status?: ResourceProcessingStatus | null;
  summary_status?: ResourceProcessingStatus | null;
};

export type ResourceSearchResponse = {
  results: ResourceSearchResult[];
  counts: { all: number; video: number; image: number; doc: number; audio: number; pdf: number };
  next_cursor: string | null;
};

export interface Channel {
  id: string;
  team_id: string;
  type: 'dm' | 'group' | 'public';
  history_mode: 'shared' | 'joined';
  name: string | null;
  topic: string | null;
  last_message_seq: string;
  unread: number;
  mentions: number;
  created_at: string;
}

/** One conversation member (user or agent) with its group role. */
export interface ConversationMember {
  member_type: 'user' | 'agent';
  user_id: string | null;
  agent_id: string | null;
  role: 'owner' | 'admin' | 'member' | string;
  name: string | null;
  email: string | null;
  agent_slug: string | null;
  joined_at: string;
}

export interface ChatMessage {
  id: string;
  channel_id: string;
  conversation_id?: string;
  seq: string;
  sender_id: string | null;
  sender_type: 'user' | 'agent' | 'system';
  content_type: 'text' | 'image' | 'media_card' | 'task_card' | 'system';
  type?: 'text' | 'image' | 'media_card' | 'task_card' | 'system';
  body: Record<string, unknown>;
  reply_to_id: string | null;
  parent_id?: string | null;
  edited_at: string | null;
  deleted_at: string | null;
  created_at: string;
}

export interface SocialAccount {
  id: string; // Snowflake BIGINT, serialized as string by backend (JS 2^53 precision)
  scope_type: 'user' | 'team';
  scope_id: string;
  platform: 'douyin' | 'kuaishou' | 'xiaohongshu';
  // The identity key the account is upserted on. One authoritative source per
  // platform (a cookie: douyin uid_tt / bilibili DedeUserID) — never the
  // on-screen handle, which is what bound one account into two rows on
  // 2026-08-09. Not user-visible; show `username` / `platform_handle`.
  platform_user_id: string;
  // The platform's public account name (抖音号 / 小红书号), mig 414. Display
  // only and free to change — it takes no part in identity. null = the console
  // did not render it on the last scrape.
  platform_handle?: string | null;
  username: string;
  avatar_url: string | null;
  token_expires_at: string | null;
  // How the account is bound (mig 401). 'oauth' = open-platform token;
  // 'session' = browser session, the only channel that can publish unattended.
  auth_type: 'oauth' | 'session';
  // Both 'expired' (oauth token) and 'needs_relogin' (dead browser session)
  // are actionable — anything counting "needs attention" must include both.
  status: 'active' | 'expired' | 'needs_relogin';
  // Last successful session validation; null = never checked (session accounts only).
  session_checked_at?: string | null;
  created_at: string;
}

/**
 * QR-login progress, as written by the session_login workflow into
 * `task_tracking.metadata.login`. The frontend reads it over Supabase
 * Realtime — there is deliberately no SSE/polling channel for this.
 *
 * Every member of the union is terminal-or-actionable, and the modal must
 * render an action for each one (CLAUDE.md: a trigger path with no typed,
 * user-visible failure branch is a silent no-op). In particular
 * `proxy_failed` is split out from `failed` because "your egress proxy is
 * down" and "the platform rejected this account" need different fixes.
 */
export type SessionLoginStatus =
  | 'waiting_scan'
  | 'scanned'
  | 'qrcode_expired'
  /**
   * The platform interrupted the scan with its own identity check and is
   * showing a menu of verification methods. **Nothing has been sent to the
   * user's phone in this state** — the browser service is picking "receive an
   * SMS" on their behalf, and only what follows is `sms_required`.
   */
  | 'identity_challenge'
  /**
   * The platform has no QR sign-in at all and we do not have a phone number to
   * text yet. Distinct from `sms_required` for the same reason
   * `identity_challenge` is: that status licenses "enter the code you were
   * sent", and saying it before anyone has supplied a number leaves the user
   * waiting on a message that could not have been sent — nobody knew where to
   * send it. Xiaohongshu's creator platform is this case.
   */
  | 'phone_required'
  | 'sms_required'
  | 'success'
  | 'timeout'
  | 'failed'
  | 'proxy_failed';

export interface SessionLoginState {
  platform: string;
  status: SessionLoginStatus;
  /** `data:image/png;base64,…` — absent once the code is scanned or dead. */
  qrcode_data_url?: string | null;
  expires_at?: string | null;
  /** Server-authored detail (English); shown verbatim under the status line. */
  message?: string | null;
  /**
   * Typed failure context (spec §7.8). `error_kind` marks a failure as **ours**
   * — the browser container, the token, the decrypt — as opposed to a verdict
   * about the account. The distinction decides what the user is told to do, so
   * a `failed` without it means the platform really did say no.
   */
  /**
   * `code_requested` is the browser service's evidence that **one of its own
   * clicks asked the platform to send a code** — not that a code field is on
   * screen. Only that licenses "we asked the platform to text you"; without
   * it the copy has to stay neutral, because `sms_required` on its own once
   * printed "the platform sent a code to this account" over a screen where
   * nothing had been sent and nobody had asked.
   */
  detail?: {
    error_kind?: string;
    reason?: string;
    code_requested?: boolean;
  } & Record<string, unknown>;
}

export interface PublishTaskAccount {
  id: string;
  account_id: string;
  username: string;
  avatar_url: string | null;
  // Mirrors publish_task_accounts.channel (mig 403). A 'session' row never
  // reaches 'pending_share' — that state only exists for the H5 phone handoff.
  channel: 'official' | 'h5' | 'session';
  status: 'pending' | 'pending_share' | 'publishing' | 'success' | 'failed' | 'cancelled';
  error_message: string | null;
  published_url: string | null;
  platform_item_id: string | null;
  published_at: string | null;
  /**
   * Did the platform confirm this post is actually live? (`publish_readback`)
   *
   * `status: 'success'` only means OUR upload finished — for the session
   * channel the platform still has to accept it, which a scheduled read-back
   * checks minutes later. Until this field existed the two were indistinguishable
   * on screen, so a post awaiting confirmation and one confirmed live both read
   * "Published".
   *
   * `null` = not checked yet (the starting point of `pending`), NOT "no check
   * needed" — only `not_supported` means that.
   *
   * ⚠️ `not_live` ("we looked, it is not up") and `pending` ("this round could
   * not tell us") must stay distinguishable all the way to the pixels. Folding
   * them together renders a crashed container as "the platform rejected your
   * post" — see the module docstring of `publish_readback.py`.
   */
  verify_state?: 'pending' | 'verified' | 'not_live' | 'not_supported' | 'abandoned' | null;
  /** Typed reason (`[rejected] …`, `[under_review] …`, `[verification_abandoned] …`).
   *  The bracketed code is the contract; the prose after it is written for logs. */
  verify_detail?: string | null;
}

/**
 * Douyin 自主声明 — the six options the platform offers, in the platform's own
 * wording. These strings are the WIRE values (and what the DB stores): the
 * browser service picks the option by matching this text on the creator page,
 * so a translated value would select nothing and the post would go out with no
 * declaration at all. English labels live in the Publish form, display only.
 *
 * Mirrors backend `services/distribution/publish_options.py::SELF_DECLARATIONS`,
 * which rejects anything outside this set.
 */
export type SelfDeclaration =
  | '内容由AI生成'
  | '内容为个人观点或见解'
  | '内容为转载信息'
  | '内容含营销推广信息'
  | '虚构演绎，仅供娱乐'
  | '无需添加自主声明';

/**
 * A topic name bound to the platform's own topic ENTITY (Douyin challenge
 * `cid`), captured at the moment the user picked it out of the suggestions.
 *
 * Parallel to `topics` (the plain names), never a replacement: hand-typed
 * topics have no id and simply do not appear here. The publish path does not
 * read this yet — it is stored because the cid exists only at pick time and
 * cannot be reconstructed afterwards.
 */
export interface TopicRef {
  name: string;
  topic_id: string;
  /** Cumulative play count as shown when the user picked it. */
  view_count: number;
}

export interface PublishTask {
  id: string;
  content_type: 'video' | 'images' | 'article';
  title: string;
  description: string | null;
  topics: string[];
  topic_refs?: TopicRef[];
  visibility: 'public' | 'friends' | 'private';
  distribution_mode: 'broadcast' | 'one_to_one';
  status: 'pending' | 'publishing' | 'pending_share' | 'success' | 'partial' | 'failed';
  created_at: string;
  /** Platform-side scheduled publish time (ISO). null = published immediately. */
  scheduled_at: string | null;
  /**
   * Can that schedule still be honoured RIGHT NOW? Derived server-side.
   *
   * - `none` — not a scheduled batch
   * - `pending` — still inside the window; re-running it as scheduled can work
   * - `unreachable` — the time has passed (or is too close to finish the
   *   upload), so re-running it as scheduled is guaranteed to be rejected
   *
   * Computed by the backend because the threshold (`SCHEDULE_MIN_LEAD`) is the
   * backend's constant. Subtracting a lead the frontend guessed at would be a
   * second copy of the rule with nothing keeping it in step.
   */
  schedule_state: 'none' | 'pending' | 'unreachable';
  /** null = the declaration control was left untouched. Distinct from
   *  '无需添加自主声明', which is the user explicitly declaring nothing. */
  self_declaration: SelfDeclaration | null;
  collection_name: string | null;
  /** Douyin 选择音乐 — the track name that was searched for at publish time.
   *  null = the music control was left untouched, i.e. the platform default
   *  (原声), which is what every post published before this field existed got. */
  music_name: string | null;
  /** The picked track's identity, when the user chose one from the platform's
   *  catalogue rather than typing a name. null = the typed-name path (or no
   *  music at all). */
  music_ref: MusicRef | null;
  accounts: PublishTaskAccount[];
}

/**
 * A track's identity as the platform states it (mig 429).
 *
 * `duration` is SECONDS and `user_count` is the raw integer — formatting is
 * the UI's job. Both ride along because they are the fingerprint the browser
 * aligns dialog rows against: same title, different author or length, is a
 * different upload.
 */
export interface MusicRef {
  music_id: string;
  music_name: string;
  music_author: string;
  duration: number;
  user_count: number;
  cover_url: string;
}

export interface PublishRequest {
  /**
   * Which workspace this batch belongs to — the active team's Snowflake, as a
   * STRING (never Number()'d).
   *
   * The personal workspace is not team-less: it IS a team row
   * (`teams.kind='personal'`), so `useWorkspaceScope().scopeId` is the right
   * value in both workspaces. Omitting it is how every publish_task ended up
   * with `team_id = NULL`, which left the issue mirrored from it with no team
   * — and the To-do list filters every scope by `team_id`, so those work items
   * were invisible to the person who created them.
   *
   * Optional on the wire: the backend falls back to the caller's personal team
   * (and validates membership when it IS supplied — this is a claim, not an
   * authorization).
   */
  team_id?: string;
  content_type?: 'video' | 'images' | 'article';
  resource_ids: string[];
  title: string;
  description?: string;
  topics?: string[];
  /**
   * Entity bindings for whichever of `topics` came from the suggestion
   * dropdown. Omit when nothing was picked from it — the backend treats a
   * missing list and an empty one the same way.
   */
  topic_refs?: TopicRef[];
  visibility?: 'public' | 'friends' | 'private';
  ai_content?: boolean;
  allow_download?: boolean;
  distribution_mode?: 'broadcast' | 'one_to_one';
  /**
   * Requested route. The backend re-decides it PER ACCOUNT (`decide_channel`),
   * so this is a preference, not an instruction: asking for `'session'` still
   * sends an OAuth-bound account down the H5 handoff.
   *
   * `'session'` was missing here while the response type already had it — the
   * same shape of gap that made the backend's `Channel` Literal silently reject
   * the only value reaching the session publish path.
   */
  channel?: 'official' | 'h5' | 'session';
  account_ids: string[];
  /** Per-account overrides keyed by account_id — e.g. a custom title for one account. */
  account_configs?: Record<string, { title?: string; description?: string; topics?: string[] }>;
  /**
   * Platform-side scheduled publish time (ISO 8601 **with offset** — a naive
   * string is rejected rather than guessed at, because getting the zone wrong
   * sends the post hours early and there is no taking it back).
   * Window: 2 hours .. 14 days from now. Omit = publish immediately.
   */
  scheduled_at?: string;
  /**
   * Douyin 自主声明. Omit = leave the control untouched — NOT the same as
   * '无需添加自主声明' (an explicit "nothing to declare" the platform records).
   * Omitting it while `ai_content` is true still produces '内容由AI生成' at
   * publish time; see the backend's `resolve_self_declaration`.
   */
  self_declaration?: SelfDeclaration;
  /** Douyin 合集 name. Matched against the account's EXISTING collections; never
   *  creates one. Omit = no collection. */
  collection_name?: string;
  /**
   * Douyin 选择音乐 — a track name. At publish time the browser opens the
   * editor's music dialog, searches for this, and selects a result: an exact
   * title if the platform returned one, otherwise the first result (reported
   * back either way). A search that returns NOTHING fails that account's row
   * rather than publishing without music — a post the user meant to have music
   * going out silently on 原声 is the outcome this field exists to prevent.
   * Omit = leave the control untouched.
   */
  music_name?: string;
  /**
   * The identity of a track picked out of the platform's own catalogue.
   *
   * A title is not an identity — one search for 「起风了」 returns five rows
   * whose titles are character-identical under different ids (measured
   * 2026-08-15). With this present the browser aligns rows on
   * (title, author, length) and refuses anything short of a unique match
   * (`music_ambiguous`); with only `music_name` it stays on the older
   * deliberately-fuzzy path, which is the right behaviour for a name someone
   * typed from memory.
   *
   * `music_id` is the platform's `id_str` and is a STRING: the sibling `id`
   * is a JSON number past 2^53 and is already a different number by the time
   * it reaches this file.
   */
  music_ref?: MusicRef;
  /**
   * The two covers produced by `POST /distribution/covers/select`. Sent at
   * create time because the usual order is cover-first: the compose form
   * derives them before a publish task exists. Omit = the platform picks its
   * own frame during publish.
   */
  cover_vertical_resource_id?: string;
  cover_horizontal_resource_id?: string;
}

/**
 * One candidate cover frame sampled from a video.
 *
 * Mirrors the backend `CoverCandidateOut`. It is NOT an HTTP response shape:
 * the list travels through `task_tracking.metadata.cover_frames` over Supabase
 * Realtime (extraction is a DBOS workflow — download + ffmpeg).
 *
 * A candidate is NOT a resource. Frames used to be persisted one row each,
 * inheriting the source video's folder — which put them in the user's Library
 * next to real material with nothing structural to tell them apart. They are
 * now purely transient: the preview arrives inline, and picking one sends its
 * `timestamp_seconds` back so the server re-reads that same frame full size.
 */
export interface CoverCandidate {
  /** Position within this sampling run — a stable React key. */
  index: number;
  /** Where in the source video this frame came from. Required: it is the only
   *  coordinate `/covers/select` accepts, because candidate frames are never
   *  persisted. Frames the extractor could not timestamp are rejected server
   *  side with a typed error rather than shown as tiles that do nothing. */
  timestamp_seconds: number;
  /** `data:image/jpeg;base64,...` — the preview goes straight into <img src>.
   *  Small on purpose (240px wide, ≤14KB) because it rides through
   *  `task_tracking.metadata` over Realtime. */
  preview_data_url: string;
  preview_width: number;
  preview_height: number;
  /**
   * DEPRECATED — the persisted frame row an OLD backend still writes.
   *
   * Present only during the deploy-skew window, and that window is not
   * hypothetical: the frontend ships from a managed runner in ~4 minutes while
   * the backend builds on the self-hosted box for ~10+, so a new UI talking to
   * the previous backend is the LIKELY intermediate state, not the unlikely
   * one. Without this the strip would render broken images and every pick
   * would 422. `CoverPicker` prefers `preview_data_url` and only falls back
   * here. Delete once a release has passed.
   */
  resource_id?: string;
}

/**
 * The `metadata.cover_frames` blob the extraction workflow writes.
 *
 * Three observable shapes, and the UI has to tell them apart:
 *  - seeded by the endpoint at create time → `candidates: []`, no `error`
 *  - success → `candidates` non-empty
 *  - failure → `error` + `error_status` (the workflow's typed failure, e.g.
 *    504 timeout vs 422 unreadable source), `candidates` still empty
 */
export interface CoverFramesMeta {
  source_resource_id: string;
  duration_seconds?: number | null;
  candidates: CoverCandidate[];
  error?: string;
  error_status?: number;
}

/** `POST /distribution/covers/select` — one picked frame → 3:4 + 4:3 covers. */
export interface CoverSelectResult {
  cover_vertical_resource_id: string;
  cover_horizontal_resource_id: string;
  publish_task_id?: string | null;
}

export interface LibraryVideo {
  id: string;
  filename: string;
  thumbnail_url: string | null;
  /** Resource mime type — lets the publish picker tell a gallery entity
   *  (`application/x-mediahub-gallery`) apart from a plain image. Optional
   *  so existing video callers stay unaffected. */
  mime_type?: string | null;
  /** Child-image count when this row is a gallery entity; 0 / absent
   *  otherwise (projected by the backend `get_resource_items` query). */
  gallery_count?: number;
  // ── Identifying metadata ────────────────────────────────────────────
  //
  // Filename alone cannot tell two versions of the same content apart — the
  // publish picker's whole job is choosing between them. These four come free
  // off the wire: the backend already returns the entire `resources` row via
  // `row_to_json(r.*)`, so this is a mapper widening, not a new query.
  //
  // Every one is nullable ON PURPOSE. Coverage on real data (204 videos):
  // duration 203, resolution 201, size 204, created_at 204 — high but not
  // total, and the generated-media tab supplies none of them. A missing value
  // renders as an em dash; it is never guessed or back-computed.
  /** Playback length in whole seconds. */
  duration_seconds?: number | null;
  /** Pixel dimensions as stored, e.g. `"1080x1920"`. */
  resolution?: string | null;
  /** File size in bytes. */
  file_size_bytes?: number | null;
  /** When the resource itself was created (NOT the library-item join row). */
  created_at?: string | null;
}
