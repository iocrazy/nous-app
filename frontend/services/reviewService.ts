import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

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

// ─── Comments ───────────────────────────────────────────

export async function fetchComments(
  resourceId: string,
  versionId?: string,
  status?: string,
): Promise<ReviewComment[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ resource_id: resourceId });
  if (versionId) params.set('version_id', versionId);
  if (status) params.set('status', status);

  const response = await fetch(`${apiUrl}/api/v1/reviews/comments?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch comments');
  const data = await response.json();
  return data.data || [];
}

export async function createComment(payload: CreateCommentPayload): Promise<ReviewComment> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/reviews/comments`, {
    method: 'POST',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Failed' }));
    throw new Error(err.detail || 'Failed to create comment');
  }
  const data = await response.json();
  return data.data;
}

export async function updateComment(
  commentId: string,
  updates: { content?: string; status?: string },
): Promise<ReviewComment> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/reviews/comments/${commentId}`, {
    method: 'PATCH',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw new Error('Failed to update comment');
  const data = await response.json();
  return data.data;
}

export async function resolveComment(commentId: string): Promise<ReviewComment> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/reviews/comments/${commentId}/resolve`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to resolve comment');
  const data = await response.json();
  return data.data;
}

export async function reopenComment(commentId: string): Promise<ReviewComment> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/reviews/comments/${commentId}/reopen`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to reopen comment');
  const data = await response.json();
  return data.data;
}

export async function deleteComment(commentId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/reviews/comments/${commentId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete comment');
}

export async function fetchCommentCount(
  resourceId: string,
  versionId?: string,
): Promise<number> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ resource_id: resourceId });
  if (versionId) params.set('version_id', versionId);

  const response = await fetch(`${apiUrl}/api/v1/reviews/comments/count?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch comment count');
  const data = await response.json();
  return data.data?.count ?? 0;
}

// ─── Review Status ──────────────────────────────────────

export async function setReviewStatus(payload: {
  resource_id: string;
  version_id?: string;
  status: string;
  comment?: string;
}): Promise<ReviewStatus> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/reviews/status`, {
    method: 'POST',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error('Failed to set review status');
  const data = await response.json();
  return data.data;
}

export async function fetchReviewStatuses(
  resourceId: string,
  versionId?: string,
): Promise<ReviewStatus[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ resource_id: resourceId });
  if (versionId) params.set('version_id', versionId);

  const response = await fetch(`${apiUrl}/api/v1/reviews/status?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch review statuses');
  const data = await response.json();
  return data.data || [];
}
