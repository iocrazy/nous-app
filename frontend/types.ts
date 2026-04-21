
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

export type ViewState = 'parser' | 'dashboard' | 'settings' | 'cleanup' | 'points' | 'mediatrack' | 'resources' | 'members' | 'billing' | 'todolist' | 'shared';

export interface ApiKey {
  id: number;
  key_id: string;        // Identifier for API calls
  key_prefix: string;    // Display prefix like dk_xxxx...
  key_value?: string;    // Full key (always accessible)
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
  scope_type: 'personal' | 'team';
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
  thumbnail_path: string | null;
  cover_image_path: string | null;
  current_version: number;
  notes: string | null;
  url: string | null;
  rating: number; // 0-5
  // Per-user download status
  video_download_status: string | null;
  music_download_status: string | null;
  cover_download_status: string | null;
  image_download_status: string | null;
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
  // Joined
  tags?: Tag[];
  folder_name?: string;
}

// Resource item (resource <-> workspace mapping)
export interface ResourceItem {
  id: string;
  resource_id: string;
  scope_type: 'personal' | 'team';
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
  sort_order?: number;
  user_id?: string;
  video_count?: number;
  media_count?: number;
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
  api_key?: string;
  app_id?: string;
  base_url?: string;
  models?: string[];
  selected_model?: string;
  summary_model?: string;
  analysis_model?: string;
}

export interface AISettings {
  ai_enabled: boolean;
  auto_transcribe: boolean;
  auto_summarize: boolean;
  preferred_language: string;
  providers: {
    openai?: AIProviderConfig;
    deepseek?: AIProviderConfig;
    doubao?: AIProviderConfig;
    volcengine?: AIProviderConfig;
    ollama?: AIProviderConfig;
    lmstudio?: AIProviderConfig;
  };
  task_assignment: {
    transcription: string;  // provider key
    summarization: string;
    visual_analysis: string;
    image_generation?: string;  // storyboard image provider
    script_generation?: string;  // storyboard script/prompt LLM
  };
}

// Nous Platform Model (admin-configured, pay with points)
export interface NousModelPublic {
  name: string;
  display_name: string;
  category: 'transcription' | 'summarization' | 'analysis';
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
  file_count: number;
  display_code?: string;
  modules_enabled?: string[];
  created_at: string;
  updated_at: string;
}

export interface ProjectMember {
  id: string;
  project_id: string;
  user_id: string;
  role: 'admin' | 'editor' | 'viewer';
  invited_by: string | null;
  email?: string;
  created_at: string;
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
  settings_json?: Record<string, unknown>;
  viewport_json?: { x: number; y: number; zoom: number };
  status: 'active' | 'archived' | 'deleted';
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
}

export type ProjectTab = 'files' | 'scripts' | 'storyboard' | 'output' | 'tasks' | 'skills' | 'shares' | 'trash';

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

export interface AILibraryAgent {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
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
  team_id?: number;
  project_id?: number;
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
  updated_at: string;
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
  team_id?: number;
  project_id?: number;
  fork_from?: string;
}
