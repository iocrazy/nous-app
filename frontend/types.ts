
export enum DownloadStatus {
  PENDING = 'PENDING',
  PROCESSING = 'PROCESSING',
  COMPLETED = 'COMPLETED',
  FAILED = 'FAILED',
  SKIPPED = 'SKIPPED'
}

// AI processing status for transcript/summary/visual analysis
export type AIStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'skipped';

export interface Video {
  // Video Identity
  id?: string;  // UUID primary key
  platform_id: string;
  user_id?: string;

  // Platform info
  source_platform?: string;  // 'douyin', 'youtube', 'bilibili', 'twitter', 'other'
  source_url?: string;       // Original user input URL
  external_id?: string;      // Platform's original ID

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
  need_download_video?: boolean;
  video_download_urls?: string[];
  image_download_urls?: string[];

  // Audio Info
  music_download_urls?: string[];
  music_name?: string;
  need_download_music?: boolean;

  // Cover Info
  cover_urls?: string[];
  dynamic_cover_url?: string;
  need_download_cover?: boolean;
  cover_download_status?: DownloadStatus;
  cover_download_path?: string;

  // Tracking
  video_download_status?: DownloadStatus;
  music_download_status?: DownloadStatus;
  download_duration?: number;
  download_path?: string;
  error_message?: string;
  download_time?: string;

  // User Data
  notes?: string;
  tags?: string[];

  // AI Generated Content (legacy)
  ai_extract_text?: string;
  ai_rewrite_text?: string;
  ai_analyze_text?: string;
  ai_generated_at?: string;

  // AI Processing Status (new)
  transcript_status?: AIStatus;
  summary_status?: AIStatus;
  visual_analysis_status?: AIStatus;
  transcript_bool?: boolean;
  summary_bool?: boolean;

  // HLS Streaming
  hls_path?: string;
  media_format?: 'mp4' | 'hls';

  // Summary preview (joined from video_summaries)
  summary_text?: string;

  // Timestamps
  created_at?: string;
  updated_at?: string;
}

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
export type DouyinBase = Video;

export type ViewState = 'parser' | 'library' | 'dashboard' | 'settings' | 'cleanup';

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

export interface VideoListResponse {
  success: boolean;
  count: number;
  videos: Video[];
}

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
  created_at: string;
}

export interface TeamMember {
  team_id: string;
  user_id: string;
  role: 'owner' | 'member';
  joined_at: string;
  // Joined from auth.users
  email?: string;
  name?: string;
}

// Collection types
export interface Collection {
  id: string;
  name: string;
  owner_id: string;
  team_id: string | null;
  created_at: string;
  // Computed
  video_count?: number;
  is_shared?: boolean;
  thumbnail_url?: string; // First video's cover
}

export interface CollectionVideo {
  collection_id: number;
  video_id: number;
  added_by: string;
  added_at: string;
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
  user_id?: string;
  video_count?: number;
  created_at: string;
}

// Smart Collections
export interface SmartCollection {
  id: number;
  name: string;
  description: string | null;
  icon: string | null;
  color: string | null;
  rules: CollectionRules;
  is_preset: boolean;
  is_active: boolean;
  sort_by: string;
  sort_order: 'asc' | 'desc';
  video_count: number;
  created_at: string;
  updated_at: string;
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
  video_id: number;
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
  video_id: number;
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
export interface VideoAnalysis {
  video_id: number;
  platform_id: string;
  visual_analysis: string | null;
  content_categories: string[];
  detected_objects: string[];
  scene_description: string | null;
  suggested_tags: string[];
  analyzed_at: string | null;
}

// AI Provider settings
export interface AIProviderConfig {
  enabled: boolean;
  api_key?: string;
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
    ollama?: AIProviderConfig;
    lmstudio?: AIProviderConfig;
  };
  task_assignment: {
    transcription: string;  // provider key
    summarization: string;
    visual_analysis: string;
  };
}
