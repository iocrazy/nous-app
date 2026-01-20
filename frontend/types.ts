
export enum DownloadStatus {
  PENDING = 'PENDING',
  PROCESSING = 'PROCESSING',
  COMPLETED = 'COMPLETED',
  FAILED = 'FAILED',
  SKIPPED = 'SKIPPED'
}

export interface DouyinBase {
  // Video Identity
  aweme_id: string;
  user_id?: string;

  // Interaction Data
  video_digg_count?: number;
  video_comment_count?: number;
  video_share_count?: number;
  video_collect_count?: number;

  // Metadata
  video_original_url: string;
  video_duration?: string; // seconds
  video_resolution?: string;
  video_datasize?: string; // bytes
  video_hashtag_name?: string;
  video_created_time?: string; // datetime string
  author?: string;
  video_title?: string;
  aweme_type?: string;
  video_desc?: string;

  // Download Info
  need_download_video?: boolean;
  video_download_urls?: string[];
  image_download_urls?: string[];

  // Audio Info
  music_download_urls?: string[];
  music_name?: string;
  need_download_music?: boolean;

  // Cover Info (added to match backend schema)
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
  video_categories?: string;

  // User Data
  notes?: string;
  tags?: string[];

  // AI Generated Content
  ai_extract_text?: string;
  ai_rewrite_text?: string;
  ai_analyze_text?: string;
  ai_generated_at?: string;

  // Timestamps
  created_at?: string;
  updated_at?: string;
}

export type ViewState = 'parser' | 'library' | 'dashboard' | 'settings' | 'cleanup';

export interface ApiKey {
  id: string;
  name: string;
  key: string;
  status: 'active' | 'inactive';
  created_at: string;
  expires_at: string; // 'Never' or ISO date
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
  videos: DouyinBase[];
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
  id: number;
  name: string;
  color: string | null;
  description: string | null;
  video_count: number;
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
  field: 'tag' | 'author' | 'date' | 'title' | 'description' | 'aweme_type' | 'view_count';
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
  aweme_id: string;
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
  aweme_id: string;
  visual_analysis: string | null;
  content_categories: string[];
  detected_objects: string[];
  scene_description: string | null;
  suggested_tags: string[];
  analyzed_at: string | null;
}
