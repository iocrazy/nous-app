// frontend/services/permissionService.ts

/**
 * Permission API service
 *
 * Fetches effective role and capabilities from the backend
 * ReBAC permission system.
 */

import { apiClient } from './apiClient';

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
  const json = await apiClient.get<
    EffectivePermission | { data: EffectivePermission }
  >('/api/v1/resources/permissions', {
    query: {
      object_type: objectType,
      object_id: objectId,
      team_id: teamId,
    },
  });
  return 'data' in json ? json.data : json;
};
