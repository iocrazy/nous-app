import type {
  Envelope,
  ResourceReviewComment,
  ResourceReviewCommentCreated,
  ResourceReviewStatus,
  ResourceReviewThread,
} from '../types/api';
import { apiClient } from './apiClient';

// ─── Types ──────────────────────────────────────────────
// Response shapes are the generated aliases in types/api.ts (P7 reviews).
// Ids are Snowflake BIGINTs sent as JSON numbers.

export interface CreateCommentPayload {
  resource_id: string;
  version_id?: string;
  content: string;
  timecode?: number;
  frame_number?: number;
  parent_id?: string;
  annotations?: Array<{
    tool_type: string;
    data: Record<string, unknown>;
  }>;
}

// ─── Comments ───────────────────────────────────────────

export async function fetchComments(
  resourceId: string,
  versionId?: string,
  status?: string,
): Promise<ResourceReviewThread[]> {
  const response = await apiClient.get<Envelope<ResourceReviewThread[]>>(
    '/api/v1/reviews/comments',
    {
      query: { resource_id: resourceId, version_id: versionId, status },
    },
  );
  return response.data || [];
}

export async function createComment(
  payload: CreateCommentPayload,
): Promise<ResourceReviewCommentCreated> {
  const response = await apiClient.post<Envelope<ResourceReviewCommentCreated>>(
    '/api/v1/reviews/comments',
    payload,
  );
  if (!response.data) throw new Error('Empty response from createComment');
  return response.data;
}

export async function resolveComment(commentId: string | number): Promise<ResourceReviewComment> {
  const response = await apiClient.post<Envelope<ResourceReviewComment>>(
    `/api/v1/reviews/comments/${commentId}/resolve`,
  );
  if (!response.data) throw new Error('Empty response from resolveComment');
  return response.data;
}

export async function reopenComment(commentId: string | number): Promise<ResourceReviewComment> {
  const response = await apiClient.post<Envelope<ResourceReviewComment>>(
    `/api/v1/reviews/comments/${commentId}/reopen`,
  );
  if (!response.data) throw new Error('Empty response from reopenComment');
  return response.data;
}

export async function deleteComment(commentId: string | number): Promise<void> {
  await apiClient.delete(`/api/v1/reviews/comments/${commentId}`);
}

// ─── Review Status ──────────────────────────────────────

export async function setReviewStatus(payload: {
  resource_id: string;
  version_id?: string;
  status: string;
  comment?: string;
}): Promise<ResourceReviewStatus> {
  const response = await apiClient.post<Envelope<ResourceReviewStatus>>(
    '/api/v1/reviews/status',
    payload,
  );
  if (!response.data) throw new Error('Empty response from setReviewStatus');
  return response.data;
}

export async function fetchReviewStatuses(
  resourceId: string,
  versionId?: string,
): Promise<ResourceReviewStatus[]> {
  const response = await apiClient.get<Envelope<ResourceReviewStatus[]>>(
    '/api/v1/reviews/status',
    {
      query: { resource_id: resourceId, version_id: versionId },
    },
  );
  return response.data || [];
}
