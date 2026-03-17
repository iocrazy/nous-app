/**
 * Cleanup Service - Storage cleanup suggestions and actions
 */

import { getAuthHeaders } from './parserService';

// API configuration - empty string means use relative paths (via Vite proxy)
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return (import.meta.env.VITE_API_URL || '').trim();
  }
  return 'http://localhost:8080';
};

// Types
export type CleanupReason = 'never_viewed' | 'duplicate_content' | 'old_unused' | 'large_file';
export type CleanupActionType = 'delete' | 'keep_forever' | 'dismiss';

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

export interface CleanupSuggestionsResponse {
  suggestions: CleanupSuggestion[];
  total_count: number;
  total_reclaimable_bytes: number;
  categories: {
    never_viewed: number;
    old_unused: number;
    duplicate_content: number;
    large_file: number;
  };
}

export interface CleanupStats {
  total_videos: number;
  total_storage_bytes: number;
  videos_never_viewed: number;
  videos_not_viewed_30_days: number;
  potential_duplicates: number;
  videos_marked_keep: number;
  reclaimable_bytes: number;
}

export interface CleanupDataResponse {
  suggestions: CleanupSuggestion[];
  total_count: number;
  total_reclaimable_bytes: number;
  categories: {
    never_viewed: number;
    old_unused: number;
    duplicate_content: number;
    large_file: number;
  };
  stats: CleanupStats;
}

export interface StorageBreakdown {
  by_type: {
    video: number;
    image: number;
    other: number;
  };
  by_month: Array<{
    month: string;
    count: number;
    bytes: number;
  }>;
  largest_videos: Array<{
    id: number;
    storage_size: number;
    media_type: number;
    created_at: string;
  }>;
  total_bytes: number;
  total_videos: number;
}

export interface CleanupActionResponse {
  message: string;
  media_id: number;
}

export interface BatchCleanupResponse {
  message: string;
  action: CleanupActionType;
  success_count: number;
  failed_count: number;
  failed_ids: number[];
}

/**
 * Get all cleanup data in a single optimized call
 * This is the preferred method as it reduces network round trips
 */
export const getCleanupData = async (
  limit: number = 50,
  includeDuplicates: boolean = true
): Promise<CleanupDataResponse> => {
  const apiUrl = getApiUrl();

  const params = new URLSearchParams({
    limit: limit.toString(),
    include_duplicates: includeDuplicates.toString(),
  });

  const response = await fetch(`${apiUrl}/api/v1/cleanup/data?${params}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get cleanup data' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Get cleanup suggestions
 * @deprecated Use getCleanupData instead for better performance
 */
export const getCleanupSuggestions = async (
  limit: number = 50,
  includeDuplicates: boolean = true
): Promise<CleanupSuggestionsResponse> => {
  const apiUrl = getApiUrl();

  const params = new URLSearchParams({
    limit: limit.toString(),
    include_duplicates: includeDuplicates.toString(),
  });

  const response = await fetch(`${apiUrl}/api/v1/cleanup/suggestions?${params}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get suggestions' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Get cleanup statistics
 */
export const getCleanupStats = async (): Promise<CleanupStats> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/cleanup/stats`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get stats' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Get storage breakdown
 */
export const getStorageBreakdown = async (): Promise<StorageBreakdown> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/cleanup/storage`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get storage info' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Take action on a cleanup suggestion
 */
export const takeCleanupAction = async (
  mediaId: number,
  action: CleanupActionType
): Promise<CleanupActionResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/cleanup/videos/${mediaId}/action`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ action }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to take action' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Batch cleanup action on multiple videos
 */
export const batchCleanupAction = async (
  mediaIds: number[],
  action: CleanupActionType
): Promise<BatchCleanupResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/cleanup/batch`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ media_ids: mediaIds, action }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to perform batch action' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Mark a video to keep forever
 */
export const markKeepForever = async (mediaId: number): Promise<CleanupActionResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/cleanup/videos/${mediaId}/keep`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to mark keep forever' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Remove keep forever mark from a video
 */
export const unmarkKeepForever = async (mediaId: number): Promise<CleanupActionResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/cleanup/videos/${mediaId}/keep`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to unmark keep forever' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

// Utility functions
export const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
};

export const getReasonLabel = (reason: CleanupReason): string => {
  const labels: Record<CleanupReason, string> = {
    never_viewed: 'Never Viewed',
    duplicate_content: 'Potential Duplicate',
    old_unused: 'Not Recently Viewed',
    large_file: 'Large File',
  };
  return labels[reason] || reason;
};

export const getReasonColor = (reason: CleanupReason): string => {
  const colors: Record<CleanupReason, string> = {
    never_viewed: 'text-yellow-500',
    duplicate_content: 'text-purple-500',
    old_unused: 'text-orange-500',
    large_file: 'text-red-500',
  };
  return colors[reason] || 'text-gray-500';
};
