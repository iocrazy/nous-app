
import { ParsedMedia, DownloadStatus } from '../types';
import { getSupabaseAccessToken } from '../supabaseClient';
import { getApiUrl } from '../utils/apiConfig';

// Get stored API Key
const getApiKey = (): string | null => {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage.getItem('mediahub_api_key') ||
             window.localStorage.getItem('douyin_api_key'); // legacy fallback
    }
  } catch (e) {}
  return null;
};

// Get auth token via Supabase getSession() — triggers auto-refresh if expired
const getAuthToken = async (): Promise<string | null> => {
  const token = await getSupabaseAccessToken();
  if (token) return token;
  return null;
};

// Build request headers (async — ensures fresh token via Supabase session)
const buildHeaders = async (): Promise<HeadersInit> => {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  };

  const apiKey = getApiKey();
  const authToken = await getAuthToken();

  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  } else if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }

  // Attach selected team ID for points/quota resolution
  try {
    const teamId = window.localStorage.getItem('mediahub_selected_team');
    if (teamId) {
      headers['X-Team-Id'] = teamId;
    }
  } catch (_) {}

  return headers;
};

// Export for use in other services
export const getAuthHeaders = buildHeaders;

export interface FetchOptions {
  video_bool?: boolean;
  cover_bool?: boolean;
  tag_ids?: string[];
  /** AI intents (spec 2026-09-10): sent only when true; mapped server-side to Pipeline system tags. */
  transcribe?: boolean;
  summarize?: boolean;
  analyze?: boolean;
}

/** Copy the true-valued AI intents onto a request body (false/undefined are omitted). */
export const applyIntentFields = (body: Record<string, unknown>, options: FetchOptions) => {
  for (const key of ['transcribe', 'summarize', 'analyze'] as const) {
    if (options[key]) body[key] = true;
  }
};

export interface FetchResponse {
  success: boolean;
  message: string;
  id?: string;  // UUID database ID for tag operations
  platform_id: string;
  title?: string;
  author?: string;
  media_type?: string;
  // Video/cover URLs
  video_download_urls?: string[];
  cover_urls?: string[];
  image_download_urls?: string[][];
  // Stats
  like_count?: number;
  comment_count?: number;
  share_count?: number;
  favorite_count?: number;
  // Video info
  duration?: string;
  published_at?: string;
  description?: string;
  original_url?: string;
  resolution?: string;
  // Download status
  video_download_status?: string;
  // Task ID (unified_task_id for tracking)
  task_id?: string;
  download_task_id?: string; // legacy
}

/**
 * Parse a video link via backend API
 */
export const parseShareLink = async (
  url: string,
  options: FetchOptions = {}
): Promise<FetchResponse> => {
  const apiUrl = getApiUrl();

  const body: Record<string, unknown> = {
    url,
    video_bool: options.video_bool ?? true,
    cover_bool: options.cover_bool ?? true,
  };
  if (options.tag_ids?.length) {
    body.tag_ids = options.tag_ids;
  }
  applyIntentFields(body, options);

  const response = await fetch(`${apiUrl}/api/v1/media/fetch`, {
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Batch parse video links
 */
export const parseBatchLinks = async (
  urls: string[],
  options: FetchOptions = {}
): Promise<{
  success: boolean;
  total: number;
  submitted: number;
  failed: number;
  results: Array<{ url: string; platform_id: string; status: string; data?: ParsedMedia }>;
  errors: Array<{ url: string; error: string }>;
}> => {
  const apiUrl = getApiUrl();

  const batchBody: Record<string, unknown> = {
    urls,
    video_bool: options.video_bool ?? true,
    cover_bool: options.cover_bool ?? true,
  };
  if (options.tag_ids?.length) {
    batchBody.tag_ids = options.tag_ids;
  }
  applyIntentFields(batchBody, options);

  const response = await fetch(`${apiUrl}/api/v1/media/fetch/batch`, {
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify(batchBody),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

export interface SodaTrackSummary {
  track_id: string;
  title: string | null;
  artist: string | null;
  cover_url: string | null;
  duration_ms: number | null;
  /** "track" (music) or "video" (UGC). Older responses omit it → treat as "track". */
  kind?: 'track' | 'video';
  /** True when this user has already downloaded this vid (incremental sync). */
  downloaded?: boolean;
}

/** A single selected playlist item to download, carrying its kind. */
export interface SodaDownloadItem {
  id: string;
  kind: 'track' | 'video';
}

export interface SodaPlaylistResult {
  playlist_id: string;
  total: number;
  tracks: SodaTrackSummary[];
  /** Count of tracks the user has already downloaded (incremental sync). */
  downloaded_count?: number;
  /** Count of tracks the user has NOT downloaded yet. */
  new_count?: number;
}

/**
 * Resolve a Soda Music playlist link into its track list.
 */
export const getSodaPlaylist = async (url: string): Promise<SodaPlaylistResult> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/media/soda/playlist`, {
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify({ url }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Submit a batch of Soda playlist items (tracks and/or UGC videos) to the
 * download queue (Task Center grouped by flow_id). Each item carries its kind so
 * videos route to the UGC download path; the backend stays back-compatible with
 * the legacy ``track_ids`` shape.
 */
export const downloadSodaTracks = async (
  items: SodaDownloadItem[],
  playlistTitle?: string,
): Promise<{ success: boolean; flow_id: string; submitted: number; total: number }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/media/soda/playlist/download`, {
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify({ items, playlist_title: playlistTitle }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    const detail = Array.isArray(error.detail)
      ? error.detail.map((e: { msg?: string }) => e.msg).join('; ')
      : error.detail;
    throw new Error(detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Fetch video list from backend API
 */
export const fetchVideosFromApi = async (
  skip: number = 0,
  limit: number = 20
): Promise<{
  success: boolean;
  count: number;
  media: ParsedMedia[];
}> => {
  const apiUrl = getApiUrl();

  const response = await fetch(
    `${apiUrl}/api/v1/media?skip=${skip}&limit=${limit}`,
    {
      method: 'GET',
      headers: await buildHeaders(),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Fetch statistics
 */
export const fetchStatistics = async (): Promise<{
  success: boolean;
  statistics: {
    total: number;
    pending: number;
    completed: number;
    failed: number;
    skipped: number;
    total_storage_bytes: number;
    unique_authors: number;
  };
}> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/media/statistics`, {
    method: 'GET',
    headers: await buildHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Fetch specific media types for an already-parsed media item.
 * Used by PlayerPage when user clicks Fetch Video / Fetch Audio / Fetch Cover.
 */
export interface TypeFetchResponse {
  task_id: string | null;
  types_submitted: string[];
  types_skipped: string[];
  types_subscribed: string[];
}

export const fetchMediaByType = async (
  platformId: string,
  types: string[],
): Promise<TypeFetchResponse> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/media/${platformId}/fetch`, {
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify({ types }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    const detail = Array.isArray(error.detail)
      ? error.detail.map((e: { msg?: string }) => e.msg).join('; ')
      : error.detail;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json();
};

/**
 * Re-extract audio from downloaded video file (ffmpeg -c:a copy).
 */
export const extractAudio = async (platformId: string): Promise<{
  success: boolean;
  message: string;
}> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/media/${platformId}/extract-audio`, {
    method: 'POST',
    headers: await buildHeaders(),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  return response.json();
};

/**
 * Retry download
 */
export const retryDownload = async (platformId: string): Promise<{
  success: boolean;
  message: string;
}> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/media/retry/${platformId}`, {
    method: 'POST',
    headers: await buildHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};
