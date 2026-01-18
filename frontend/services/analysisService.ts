/**
 * Analysis Service - AI-powered video analysis
 */

import { getAuthHeaders } from './parserService';

// API configuration
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL;
  }
  return 'http://localhost:8080';
};

// Types
export interface AnalysisStatus {
  video_id: number;
  aweme_id: string;
  status: 'pending' | 'analyzing' | 'completed' | 'failed';
  has_analysis: boolean;
  has_tags: boolean;
  has_embedding: boolean;
  error_message?: string;
}

export interface VideoAnalysisResult {
  video_id: number;
  aweme_id: string;
  visual_analysis: string | null;
  content_categories: string[];
  detected_objects: string[];
  scene_description: string | null;
  suggested_tags: string[];
  analyzed_at: string | null;
}

export interface AnalyzeResponse {
  message: string;
  video_id: number;
  status: string;
}

export interface BatchAnalyzeResponse {
  message: string;
  queued_count: number;
  skipped_count: number;
  video_ids: number[];
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
export const getAnalysisStatus = async (videoId: number): Promise<AnalysisStatus> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/status/${videoId}`, {
    method: 'GET',
    headers: getAuthHeaders(),
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
export const getVideoAnalysis = async (videoId: number): Promise<VideoAnalysisResult> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/video/${videoId}`, {
    method: 'GET',
    headers: getAuthHeaders(),
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
  videoId: number,
  forceReanalyze: boolean = false
): Promise<AnalyzeResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/analyze/${videoId}`, {
    method: 'POST',
    headers: getAuthHeaders(),
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
  videoIds?: number[],
  limit: number = 50,
  forceReanalyze: boolean = false
): Promise<BatchAnalyzeResponse> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/batch`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({
      video_ids: videoIds,
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
    headers: getAuthHeaders(),
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
export const getSuggestedTags = async (videoId: number): Promise<string[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/analysis/video/${videoId}/suggested-tags`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get suggested tags' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.suggested_tags || [];
};
