import { apiClient } from './apiClient';

// ─── Types ──────────────────────────────────────────────

export interface ReviewAnnotation {
  id: string;
  comment_id: string;
  tool_type: 'arrow' | 'rect' | 'freehand' | 'text';
  data: Record<string, unknown>;
  created_at: string;
}

export interface ReviewComment {
  id: string;
  resource_id: string;
  version_id: string | null;
  author_id: string;
  timecode: number | null;
  frame_number: number | null;
  content: string;
  status: 'open' | 'resolved' | 'wontfix';
  parent_id: string | null;
  created_at: string;
  updated_at: string;
  annotations?: ReviewAnnotation[];
  replies?: ReviewComment[];
}

export interface ReviewStatus {
  id: string;
  resource_id: string;
  version_id: string | null;
  reviewer_id: string;
  status: 'pending' | 'approved' | 'needs_changes' | 'rejected';
  comment: string | null;
  created_at: string;
  updated_at: string;
}

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

interface Envelope<T> {
  data?: T;
}

// ─── Comments ───────────────────────────────────────────

export async function fetchComments(
  resourceId: string,
  versionId?: string,
  status?: string,
): Promise<ReviewComment[]> {
  const response = await apiClient.get<Envelope<ReviewComment[]>>(
    '/api/v1/reviews/comments',
    {
      query: { resource_id: resourceId, version_id: versionId, status },
    },
  );
  return response.data || [];
}

export async function createComment(
  payload: CreateCommentPayload,
): Promise<ReviewComment> {
  const response = await apiClient.post<Envelope<ReviewComment>>(
    '/api/v1/reviews/comments',
    payload,
  );
  if (!response.data) throw new Error('Empty response from createComment');
  return response.data;
}

export async function updateComment(
  commentId: string,
  updates: { content?: string; status?: string },
): Promise<ReviewComment> {
  const response = await apiClient.patch<Envelope<ReviewComment>>(
    `/api/v1/reviews/comments/${commentId}`,
    updates,
  );
  if (!response.data) throw new Error('Empty response from updateComment');
  return response.data;
}

export async function resolveComment(commentId: string): Promise<ReviewComment> {
  const response = await apiClient.post<Envelope<ReviewComment>>(
    `/api/v1/reviews/comments/${commentId}/resolve`,
  );
  if (!response.data) throw new Error('Empty response from resolveComment');
  return response.data;
}

export async function reopenComment(commentId: string): Promise<ReviewComment> {
  const response = await apiClient.post<Envelope<ReviewComment>>(
    `/api/v1/reviews/comments/${commentId}/reopen`,
  );
  if (!response.data) throw new Error('Empty response from reopenComment');
  return response.data;
}

export async function deleteComment(commentId: string): Promise<void> {
  await apiClient.delete(`/api/v1/reviews/comments/${commentId}`);
}

export async function fetchCommentCount(
  resourceId: string,
  versionId?: string,
): Promise<number> {
  const response = await apiClient.get<Envelope<{ count: number }>>(
    '/api/v1/reviews/comments/count',
    {
      query: { resource_id: resourceId, version_id: versionId },
    },
  );
  return response.data?.count ?? 0;
}

// ─── Review Status ──────────────────────────────────────

export async function setReviewStatus(payload: {
  resource_id: string;
  version_id?: string;
  status: string;
  comment?: string;
}): Promise<ReviewStatus> {
  const response = await apiClient.post<Envelope<ReviewStatus>>(
    '/api/v1/reviews/status',
    payload,
  );
  if (!response.data) throw new Error('Empty response from setReviewStatus');
  return response.data;
}

export async function fetchReviewStatuses(
  resourceId: string,
  versionId?: string,
): Promise<ReviewStatus[]> {
  const response = await apiClient.get<Envelope<ReviewStatus[]>>(
    '/api/v1/reviews/status',
    {
      query: { resource_id: resourceId, version_id: versionId },
    },
  );
  return response.data || [];
}
