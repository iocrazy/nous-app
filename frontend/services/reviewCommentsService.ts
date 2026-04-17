// frontend/services/reviewCommentsService.ts

/**
 * Review Comments API service for share pages.
 *
 * Provides functions for fetching and creating comments
 * on review-type shares.
 */

import { apiClient } from './apiClient';

export interface ReviewComment {
  id: string;
  content: string;
  timestamp_seconds: number | null;
  visibility: string;
  author_id: string | null;
  created_at: string;
}

interface Envelope<T> {
  data?: T;
}

/**
 * Fetch comments for a share by share code.
 */
export const fetchShareComments = async (
  shareCode: string,
): Promise<ReviewComment[]> => {
  const result = await apiClient.get<Envelope<ReviewComment[]>>(
    `/api/v1/shares/code/${shareCode}/comments`,
  );
  return result.data || [];
};

/**
 * Create a comment on a review share.
 */
export const createShareComment = async (
  shareCode: string,
  data: {
    content: string;
    timecode?: number;
    visibility?: string;
  },
): Promise<ReviewComment> => {
  const result = await apiClient.post<Envelope<ReviewComment>>(
    `/api/v1/shares/code/${shareCode}/comments`,
    {
      content: data.content,
      timecode: data.timecode ?? null,
      visibility: data.visibility ?? 'all',
    },
  );
  if (!result.data) throw new Error('Empty response from createShareComment');
  return result.data;
};
