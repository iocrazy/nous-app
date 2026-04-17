/**
 * Analysis Service - AI-powered video analysis
 */

import { apiClient } from './apiClient';

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

export const getAnalysisStatus = async (
  mediaId: number,
): Promise<AnalysisStatus> =>
  apiClient.get<AnalysisStatus>(`/api/v1/analysis/status/${mediaId}`);

export const getVideoAnalysis = async (
  mediaId: number,
): Promise<VideoAnalysisResult> =>
  apiClient.get<VideoAnalysisResult>(`/api/v1/analysis/video/${mediaId}`);

export const analyzeVideo = async (
  mediaId: number,
  forceReanalyze: boolean = false,
): Promise<AnalyzeResponse> =>
  apiClient.post<AnalyzeResponse>(`/api/v1/analysis/analyze/${mediaId}`, {
    force_reanalyze: forceReanalyze,
  });

export const batchAnalyze = async (
  mediaIds?: number[],
  limit: number = 50,
  forceReanalyze: boolean = false,
): Promise<BatchAnalyzeResponse> =>
  apiClient.post<BatchAnalyzeResponse>('/api/v1/analysis/batch', {
    media_ids: mediaIds,
    limit,
    force_reanalyze: forceReanalyze,
  });

export const getAnalysisStats = async (): Promise<AnalysisStats> =>
  apiClient.get<AnalysisStats>('/api/v1/analysis/stats');

export const getSuggestedTags = async (mediaId: number): Promise<string[]> => {
  const data = await apiClient.get<{ suggested_tags?: string[] }>(
    `/api/v1/analysis/video/${mediaId}/suggested-tags`,
  );
  return data.suggested_tags || [];
};
