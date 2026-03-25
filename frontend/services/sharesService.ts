// frontend/services/sharesService.ts

/**
 * Shares API service
 *
 * Provides functions for creating, fetching, updating,
 * cancelling, and accessing shares via share code.
 */

import { getAuthHeaders } from './parserService';
import { Share } from '../types';

const API_BASE = 'VITE_API_URL' in import.meta.env ? (import.meta.env.VITE_API_URL || '') : 'http://localhost:8080';

/**
 * Create a new share.
 */
export const createShare = async (data: {
  resource_id?: string;
  project_file_id?: string;
  folder_id?: string;
  version_id?: string;
  share_type: string;
  share_name: string;
  password?: string;
  allow_download?: boolean;
  expires_at?: string;
  max_views?: number;
  watermark?: boolean;
}): Promise<Share> => {
  const url = `${API_BASE}/api/v1/shares`;

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to create share' }));
    const detail = error.detail;
    const message = Array.isArray(detail)
      ? detail.map((d: { msg?: string }) => d.msg || '').join('; ')
      : (typeof detail === 'string' ? detail : `HTTP ${response.status}`);
    throw new Error(message);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to create share');
  }
  return result.data;
};

/**
 * Fetch shares list with optional filters.
 */
export const fetchShares = async (params?: {
  resource_id?: string;
  project_file_id?: string;
  folder_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<Share[]> => {
  const searchParams = new URLSearchParams();
  if (params?.resource_id) searchParams.set('resource_id', params.resource_id);
  if (params?.project_file_id) searchParams.set('project_file_id', params.project_file_id);
  if (params?.folder_id) searchParams.set('folder_id', params.folder_id);
  if (params?.status) searchParams.set('status', params.status);
  if (params?.limit != null) searchParams.set('limit', String(params.limit));
  if (params?.offset != null) searchParams.set('offset', String(params.offset));

  const query = searchParams.toString();
  const url = `${API_BASE}/api/v1/shares${query ? `?${query}` : ''}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch shares' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to fetch shares');
  }
  return result.data;
};

/**
 * Get a single share by ID.
 */
export const getShare = async (shareId: string): Promise<Share> => {
  const url = `${API_BASE}/api/v1/shares/${shareId}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch share' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to fetch share');
  }
  return result.data;
};

/**
 * Update an existing share.
 */
export const updateShare = async (
  shareId: string,
  data: {
    share_name?: string;
    password?: string | null;
    allow_download?: boolean;
    expires_at?: string | null;
    max_views?: number | null;
    watermark?: boolean;
  }
): Promise<Share> => {
  const url = `${API_BASE}/api/v1/shares/${shareId}`;

  const response = await fetch(url, {
    method: 'PUT',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to update share' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to update share');
  }
  return result.data;
};

/**
 * Cancel (soft-delete) a share.
 */
export const cancelShare = async (shareId: string): Promise<void> => {
  const url = `${API_BASE}/api/v1/shares/${shareId}`;

  const response = await fetch(url, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to cancel share' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to cancel share');
  }
};

/**
 * Permanently delete a share (hard delete, only for expired/cancelled).
 */
export const deleteSharePermanent = async (shareId: string): Promise<void> => {
  const url = `${API_BASE}/api/v1/shares/${shareId}/permanent`;

  const response = await fetch(url, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to delete share' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Access a share by its public share code (no auth required for public access).
 * Backend uses POST with JSON body for password verification.
 */
export const accessShare = async (
  shareCode: string,
  password?: string
): Promise<Share> => {
  const url = `${API_BASE}/api/v1/shares/code/${shareCode}`;

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: password || null }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to access share' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to access share');
  }
  return result.data;
};
