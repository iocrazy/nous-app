
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

export type ViewState = 'parser' | 'library' | 'dashboard' | 'settings';

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
}

export interface CollectionVideo {
  collection_id: string;
  video_aweme_id: string;
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
