// frontend/services/reviewCommentsService.ts

/**
 * Review Comments API service for share pages.
 *
 * Provides functions for fetching and creating comments
 * on review-type shares.
 */

import { getAuthHeaders } from './parserService';

const API_BASE = 'VITE_API_URL' in import.meta.env ? (import.meta.env.VITE_API_URL || '') : 'http://localhost:8080';

export interface ReviewComment {
  id: string;
  content: string;
  timestamp_seconds: number | null;
  visibility: string;
  author_id: string | null;
  created_at: string;
}

/**
 * Fetch comments for a share by share code.
 */
export const fetchShareComments = async (shareCode: string): Promise<ReviewComment[]> => {
  const url = `${API_BASE}/api/v1/shares/code/${shareCode}/comments`;

  const response = await fetch(url, { method: 'GET' });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch comments' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
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
  const headers = await getAuthHeaders();
  const url = `${API_BASE}/api/v1/shares/code/${shareCode}/comments`;

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      ...headers,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      content: data.content,
      timecode: data.timecode ?? null,
      visibility: data.visibility ?? 'all',
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to create comment' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  return result.data;
};
