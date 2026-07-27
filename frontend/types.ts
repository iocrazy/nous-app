
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
}

/** Blocked-reason codes the advance predicate can return (spec §7). */
export type AdvanceBlockedReason =
  | 'NOT_MANAGER_OR_EDITOR'
  | 'REVIEW_PENDING'
  | 'DELIVERABLE_MISSING'
  | 'FORM_INCOMPLETE'
  | 'NO_NEXT';

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
  /** Required form-field LABELS (never keys) missing from the target group
   * when `blocked_reason === 'FORM_INCOMPLETE'` (mig 390, M3 PR-I §2). Empty
   * for every other ruling. */
  missing_fields: string[];
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
  script_count: number;
  scene_count: number;
  shots_total: number;
  shots_done: number;
  renders_count: number;
  status: string;
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
  // Agent-overrides (mig 341). Single-get: which layer produced this merged
  // view + the fields it replaced. List: override_scopes marks presets the
  // caller (or their teams) customized — drives the sidebar badge.
  override_scope?: 'user' | 'team' | null;
  override_fields?: string[];
  override_scopes?: string[];
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
  // Reverse index: agents that bind this skill. Populated by the skill
  // detail endpoint only — list endpoint leaves this empty for
  // performance.
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
export interface AgentRunEvent {
  seq: number;
  event_type: 'user' | 'assistant' | 'tool_call' | 'error' | 'system';
  payload: Record<string, unknown>;
  created_at: string;
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
  kind: string; // image | video | pdf | resource_ref
  resource_id?: string | null;
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

export interface ChatResponse {
  message: AIChatMessage;
  usage: { prompt_tokens?: number; completion_tokens?: number };
  run_id?: string | null;
  tool_calls?: ChatToolCall[];
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

/** Search result row from GET /api/v1/resources/search */
export type ResourceSearchResult = {
  id: string;
  name: string;
  kind: 'video' | 'image' | 'doc' | 'audio' | 'pdf';
  mime: string | null;
  size: number | null;
  scope: { type: 'personal' | 'team'; id: string };
  updated_at: string;
  thumbnail_url: string | null;
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
  platform_user_id: string;
  username: string;
  avatar_url: string | null;
  token_expires_at: string | null;
  status: 'active' | 'expired';
  created_at: string;
}

export interface PublishTaskAccount {
  id: string;
  account_id: string;
  username: string;
  avatar_url: string | null;
  channel: 'official' | 'h5';
  status: 'pending' | 'pending_share' | 'publishing' | 'success' | 'failed' | 'cancelled';
  error_message: string | null;
  published_url: string | null;
  platform_item_id: string | null;
  published_at: string | null;
}

export interface PublishTask {
  id: string;
  content_type: 'video' | 'images' | 'article';
  title: string;
  description: string | null;
  topics: string[];
  visibility: 'public' | 'friends' | 'private';
  distribution_mode: 'broadcast' | 'one_to_one';
  status: 'pending' | 'publishing' | 'pending_share' | 'success' | 'partial' | 'failed';
  created_at: string;
  accounts: PublishTaskAccount[];
}

export interface PublishRequest {
  content_type?: 'video' | 'images' | 'article';
  resource_ids: string[];
  title: string;
  description?: string;
  topics?: string[];
  visibility?: 'public' | 'friends' | 'private';
  ai_content?: boolean;
  allow_download?: boolean;
  distribution_mode?: 'broadcast' | 'one_to_one';
  channel?: 'official' | 'h5';
  account_ids: string[];
  /** Per-account overrides keyed by account_id — e.g. a custom title for one account. */
  account_configs?: Record<string, { title?: string; description?: string; topics?: string[] }>;
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
}
