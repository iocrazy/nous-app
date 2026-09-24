// frontend/services/sharesService.ts

/**
 * Shares API service
 *
 * Provides functions for creating, fetching, updating,
 * cancelling, and accessing shares via share code.
 *
 * Shapes come from the backend (`types/api.ts`, "P6 shares"). Failures arrive
 * as non-2xx `ErrorResponse` bodies, which `apiClient` turns into an
 * `ApiError` carrying the backend's `error` text — a 2xx body is always a
 * success, so there is nothing to unwrap but `data`.
 */

import { apiClient, apiFetch } from './apiClient';
import type {
  Envelope,
  Share,
  ShareStatusToggle,
  ShareVisitorView,
} from '../types/api';

type ShareId = Share['id'] | string;

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
  return result.data;
};

/**
 * Fetch the caller's shares with optional filters.
 */
export const fetchShares = async (params?: {
  share_type?: string;
  status?: string;
  team_id?: string;
  limit?: number;
  offset?: number;
}): Promise<Share[]> => {
  const result = await apiClient.get<Envelope<Share[]> & { count: number }>(
    '/api/v1/shares',
    { query: params ?? {} },
  );
  return result.data;
};

/**
 * Toggle a share between active and inactive. Returns the new status.
 */
export const cancelShare = async (
  shareId: ShareId,
): Promise<ShareStatusToggle['status']> => {
  const result = await apiClient.delete<ShareStatusToggle>(
    `/api/v1/shares/${shareId}`,
  );
  return result.status;
};

/**
 * Permanently delete a share (hard delete).
 */
export const deleteSharePermanent = async (shareId: ShareId): Promise<void> => {
  await apiClient.delete(`/api/v1/shares/${shareId}/permanent`);
};

/**
 * Access a share by its public share code (no auth required for public access).
 * Uses apiFetch directly because this endpoint is unauthenticated and accepts
 * password via JSON body.
 *
 * The returned `access_token` is what the media and comment routes take as
 * `share_token`; a password-protected share accepts nothing else.
 */
export const accessShare = async (
  shareCode: string,
  password?: string,
): Promise<ShareVisitorView> => {
  const response = await apiFetch(`/api/v1/shares/code/${shareCode}`, {
    method: 'POST',
    json: { password: password || null },
  });
  const result: Envelope<ShareVisitorView> = await response.json();
  return result.data;
};
