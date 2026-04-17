// frontend/services/sharesService.ts

/**
 * Shares API service
 *
 * Provides functions for creating, fetching, updating,
 * cancelling, and accessing shares via share code.
 */

import { apiClient, apiFetch } from './apiClient';
import { Share } from '../types';

interface Envelope<T> {
  success: boolean;
  message?: string;
  data: T;
}

function unwrap<T>(result: Envelope<T>): T {
  if (!result.success) {
    throw new Error(result.message || 'Request failed');
  }
  return result.data;
}

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
  const result = await apiClient.post<Envelope<Share>>('/api/v1/shares', data);
  return unwrap(result);
};

/**
 * Fetch shares list with optional filters.
 */
export const fetchShares = async (params?: {
  resource_id?: string;
  project_file_id?: string;
  folder_id?: string;
  status?: string;
  team_id?: string;
  limit?: number;
  offset?: number;
}): Promise<Share[]> => {
  const result = await apiClient.get<Envelope<Share[]>>('/api/v1/shares', {
    query: params ?? {},
  });
  return unwrap(result);
};

/**
 * Get a single share by ID.
 */
export const getShare = async (shareId: string): Promise<Share> => {
  const result = await apiClient.get<Envelope<Share>>(
    `/api/v1/shares/${shareId}`,
  );
  return unwrap(result);
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
  },
): Promise<Share> => {
  const result = await apiClient.put<Envelope<Share>>(
    `/api/v1/shares/${shareId}`,
    data,
  );
  return unwrap(result);
};

/**
 * Cancel (soft-delete) a share.
 */
export const cancelShare = async (shareId: string): Promise<void> => {
  const result = await apiClient.delete<Envelope<null>>(
    `/api/v1/shares/${shareId}`,
  );
  unwrap(result);
};

/**
 * Permanently delete a share (hard delete, only for expired/cancelled).
 */
export const deleteSharePermanent = async (shareId: string): Promise<void> => {
  await apiClient.delete(`/api/v1/shares/${shareId}/permanent`);
};

/**
 * Access a share by its public share code (no auth required for public access).
 * Uses apiFetch directly because this endpoint is unauthenticated and accepts
 * password via JSON body.
 */
export const accessShare = async (
  shareCode: string,
  password?: string,
): Promise<Share> => {
  const response = await apiFetch(`/api/v1/shares/code/${shareCode}`, {
    method: 'POST',
    json: { password: password || null },
  });
  const result: Envelope<Share> = await response.json();
  return unwrap(result);
};
