// frontend/services/permissionService.ts

/**
 * Permission API service
 *
 * Fetches effective role and capabilities from the backend
 * ReBAC permission system.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface EffectivePermission {
  role: string;
  capabilities: string[];
}

/**
 * Fetch the effective role and capabilities for the current user
 * on a specific object within a team.
 */
export const fetchEffectiveRole = async (
  objectType: string,
  objectId: string,
  teamId: string,
): Promise<EffectivePermission> => {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams({
    object_type: objectType,
    object_id: objectId,
    team_id: teamId,
  });
  const res = await fetch(`${getApiUrl()}/api/v1/resources/permissions?${params}`, {
    headers,
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch permissions: ${res.status}`);
  }
  const json = await res.json();
  return json.data ?? json;
};
