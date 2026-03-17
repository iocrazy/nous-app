/**
 * Analysis Service - AI-powered video analysis
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
export interface AnalysisStatus {
  media_id: number;
  platform_id: string;
  status: 'pending' | 'analyzing' | 'completed' | 'failed';
  has_analysis: boolean;
  has_tags: boolean;
  has_embedding: boolean;
  error_message?: string;
}

export interface VideoAnalysisResult {
  media_id: number;
  platform_id: string;
  visual_analysis: string | null;
  content_categories: string[];
  detected_objects: string[];
  scene_description: string | null;
  suggested_tags: string[];
  analyzed_at: string | null;
}

export interface AnalyzeResponse {
  message: string;
  media_id: number;
  status: string;
}

export interface BatchAnalyzeResponse {
  message: string;
  queued_count: number;
  skipped_count: number;
  media_ids: number[];
}

export interface AnalysisStats {
  total_videos: number;
  analyzed_count: number;
  pending_count: number;
  failed_count: number;
  tagged_count: number;
  embedding_count: number;
}

/**
 * Get analysis status for a video
 */
export const getAnalysisStatus = async (mediaId: number): Promise<AnalysisStatus> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/status/${mediaId}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get status' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Get full analysis results for a video
 */
export const getVideoAnalysis = async (mediaId: number): Promise<VideoAnalysisResult> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/video/${mediaId}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get analysis' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Trigger analysis for a single video
 */
export const analyzeVideo = async (
  mediaId: number,
  forceReanalyze: boolean = false
): Promise<AnalyzeResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/analyze/${mediaId}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ force_reanalyze: forceReanalyze }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to start analysis' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Trigger batch analysis for multiple videos
 */
export const batchAnalyze = async (
  mediaIds?: number[],
  limit: number = 50,
  forceReanalyze: boolean = false
): Promise<BatchAnalyzeResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/batch`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({
      media_ids: mediaIds,
      limit,
      force_reanalyze: forceReanalyze,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to start batch analysis' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/**
 * Get overall analysis statistics
 */
export const getAnalysisStats = async (): Promise<AnalysisStats> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/stats`, {
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
 * Get suggested tags for a video
 */
export const getSuggestedTags = async (mediaId: number): Promise<string[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/video/${mediaId}/suggested-tags`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get suggested tags' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.suggested_tags || [];
};
