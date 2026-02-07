
import { Video, DownloadStatus } from '../types';

// API config
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

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

// Get auth token from Supabase session
const getAuthToken = (): string | null => {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const keys = Object.keys(window.localStorage).filter(k =>
        k.startsWith('sb-') && k.endsWith('-auth-token')
      );

      const selfHostedKey = keys.find(k => !k.includes('zesczfidxsikvohrxson'));
      const keyToUse = selfHostedKey || keys[0];

      if (keyToUse) {
        const session = window.localStorage.getItem(keyToUse);
        if (session) {
          const parsed = JSON.parse(session);
          return parsed?.access_token || null;
        }
      }
    }
  } catch (e) {
    console.error('Failed to get auth token:', e);
  }
  return null;
};

// Build request headers
const buildHeaders = (): HeadersInit => {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  };

  const apiKey = getApiKey();
  const authToken = getAuthToken();

  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  } else if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }

  return headers;
};

// Export for use in other services
export const getAuthHeaders = buildHeaders;

export interface FetchOptions {
  video_bool?: boolean;
  music_bool?: boolean;
  cover_bool?: boolean;
}

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
  // Progressive download task ID (for polling progress)
  download_task_id?: string;
}

/**
 * Parse a video link via backend API
 */
export const parseShareLink = async (
  url: string,
  options: FetchOptions = {}
): Promise<FetchResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/videos/fetch`, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({
      url,
      video_bool: options.video_bool ?? true,
      music_bool: options.music_bool ?? false,
      cover_bool: options.cover_bool ?? true,
    }),
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
  results: Array<{ url: string; platform_id: string; status: string; data?: Video }>;
  errors: Array<{ url: string; error: string }>;
}> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/videos/fetch/batch`, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({
      urls,
      video_bool: options.video_bool ?? true,
      music_bool: options.music_bool ?? false,
      cover_bool: options.cover_bool ?? true,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
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
  videos: Video[];
}> => {
  const apiUrl = getApiUrl();

  const response = await fetch(
    `${apiUrl}/api/v1/videos?skip=${skip}&limit=${limit}`,
    {
      method: 'GET',
      headers: buildHeaders(),
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

  const response = await fetch(`${apiUrl}/api/v1/videos/statistics`, {
    method: 'GET',
    headers: buildHeaders(),
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

  const response = await fetch(`${apiUrl}/api/v1/videos/retry/${platformId}`, {
    method: 'POST',
    headers: buildHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};
