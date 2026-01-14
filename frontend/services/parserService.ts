
import { DouyinBase, DownloadStatus } from '../types';

// API 配置
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL;
  }
  return 'http://localhost:8080';
};

// 获取存储的 API Key
const getApiKey = (): string | null => {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage.getItem('douyin_api_key');
    }
  } catch (e) {}
  return null;
};

// 获取认证 token（从 Supabase session）
const getAuthToken = (): string | null => {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const session = window.localStorage.getItem('sb-zesczfidxsikvohrxson-auth-token');
      if (session) {
        const parsed = JSON.parse(session);
        return parsed?.access_token || null;
      }
    }
  } catch (e) {}
  return null;
};

// 构建请求头
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

export interface FetchOptions {
  video_bool?: boolean;
  music_bool?: boolean;
  cover_bool?: boolean;
  video_categories?: string;
}

export interface FetchResponse {
  success: boolean;
  message: string;
  aweme_id: string;
  video_title?: string;
  author?: string;
  aweme_type?: string;
}

/**
 * 调用后端 API 解析抖音链接
 */
export const parseShareLink = async (
  url: string,
  options: FetchOptions = {}
): Promise<FetchResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/douyin/fetch`, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({
      url,
      video_bool: options.video_bool ?? true,
      music_bool: options.music_bool ?? false,
      cover_bool: options.cover_bool ?? true,
      video_categories: options.video_categories,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * 批量解析抖音链接
 */
export const parseBatchLinks = async (
  urls: string[],
  options: FetchOptions = {}
): Promise<{
  success: boolean;
  total: number;
  submitted: number;
  failed: number;
  results: Array<{ url: string; aweme_id: string; status: string }>;
  errors: Array<{ url: string; error: string }>;
}> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/douyin/fetch/batch`, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({
      urls,
      video_bool: options.video_bool ?? true,
      music_bool: options.music_bool ?? false,
      cover_bool: options.cover_bool ?? true,
      video_categories: options.video_categories,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * 获取视频列表（从后端 API）
 */
export const fetchVideosFromApi = async (
  skip: number = 0,
  limit: number = 20
): Promise<{
  success: boolean;
  count: number;
  videos: DouyinBase[];
}> => {
  const apiUrl = getApiUrl();

  const response = await fetch(
    `${apiUrl}/api/v1/douyin/videos?skip=${skip}&limit=${limit}`,
    {
      method: 'GET',
      headers: buildHeaders(),
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * 获取统计信息
 */
export const fetchStatistics = async (): Promise<{
  success: boolean;
  statistics: {
    total: number;
    pending: number;
    completed: number;
    failed: number;
    skipped: number;
  };
}> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/douyin/statistics`, {
    method: 'GET',
    headers: buildHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * 重试下载
 */
export const retryDownload = async (awemeId: string): Promise<{
  success: boolean;
  message: string;
}> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/douyin/retry/${awemeId}`, {
    method: 'POST',
    headers: buildHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};
