// frontend/services/reviewCommentsService.ts

/**
 * Review Comments API service for share pages.
 *
 * Provides functions for fetching and creating comments
 * on review-type shares.
 *
 * `shareToken` is the `access_token` from `accessShare`. A password-protected
 * share refuses its comments without it (the share code alone proves nothing
 * about the password).
 */

import { apiClient } from './apiClient';
import type { Envelope, ShareComment, ShareCommentRow } from '../types/api';

const tokenQuery = (shareToken?: string | null) =>
  shareToken ? { share_token: shareToken } : {};

/**
 * Fetch comments for a share by share code.
 */
export const fetchShareComments = async (
  shareCode: string,
  shareToken?: string | null,
): Promise<ShareComment[]> => {
  const result = await apiClient.get<Envelope<ShareComment[]>>(
    `/api/v1/shares/code/${shareCode}/comments`,
    { query: tokenQuery(shareToken) },
  );
  return result.data;
};

/**
 * Create a comment on a review share.
 */
export const createShareComment = async (
  shareCode: string,
  data: {
    content: string;
    timecode?: number;
  },
  shareToken?: string | null,
): Promise<ShareCommentRow> => {
  const result = await apiClient.post<Envelope<ShareCommentRow>>(
    `/api/v1/shares/code/${shareCode}/comments`,
    {
      content: data.content,
      timecode: data.timecode ?? null,
    },
    { query: tokenQuery(shareToken) },
  );
  return result.data;
};
