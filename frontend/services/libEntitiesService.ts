// frontend/services/libEntitiesService.ts
//
// Generalized project library REST client (mig 358, SP1): locations + props
// in one table keyed by entity_type — the sibling of charactersService.

import { apiFetch, ApiError } from './apiClient';

export type LibEntityType = 'location' | 'prop';

export interface LibEntity {
  id: string;
  project_id: string;
  entity_type: LibEntityType;
  name: string;
  /** Per-type badge: location → interior/exterior, prop → hero/set/costume. */
  badge_tag: string;
  description: string;
  tags: Record<string, string[]>;
  cover_url: string | null;
  source: 'manual' | 'script';
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface LibEntityCreatePayload {
  name: string;
  badge_tag?: string;
  description?: string;
  tags?: Record<string, string[]>;
  cover_url?: string | null;
}

export type LibEntityUpdatePayload = Partial<LibEntityCreatePayload> & {
  sort_order?: number;
};

async function readData<T>(response: Response, what: string): Promise<T> {
  const body = (await response.json()) as { success: boolean; data?: T };
  if (!body.success || body.data === undefined) {
    throw new ApiError(`${what} response missing data`, 500);
  }
  return body.data;
}

export async function listLibEntities(
  projectId: string,
  entityType: LibEntityType,
): Promise<LibEntity[]> {
  const response = await apiFetch(`/api/v1/projects/${projectId}/lib/${entityType}`);
  return readData(response, `${entityType} list`);
}

export async function createLibEntity(
  projectId: string,
  entityType: LibEntityType,
  payload: LibEntityCreatePayload,
): Promise<LibEntity> {
  const response = await apiFetch(`/api/v1/projects/${projectId}/lib/${entityType}`, {
    method: 'POST',
    json: payload,
  });
  return readData(response, `${entityType} create`);
}

export async function updateLibEntity(
  projectId: string,
  entityType: LibEntityType,
  entityId: string,
  payload: LibEntityUpdatePayload,
): Promise<LibEntity> {
  const response = await apiFetch(
    `/api/v1/projects/${projectId}/lib/${entityType}/${entityId}`,
    { method: 'PATCH', json: payload },
  );
  return readData(response, `${entityType} update`);
}

export async function deleteLibEntity(
  projectId: string,
  entityType: LibEntityType,
  entityId: string,
): Promise<void> {
  await apiFetch(`/api/v1/projects/${projectId}/lib/${entityType}/${entityId}`, {
    method: 'DELETE',
  });
}

/** Locations only (scene-header derivation); props are manual-only (400). */
export async function extractLibEntitiesFromScript(
  projectId: string,
  entityType: LibEntityType,
): Promise<LibEntity[]> {
  const response = await apiFetch(
    `/api/v1/projects/${projectId}/lib/${entityType}/extract`,
    { method: 'POST' },
  );
  return readData(response, `${entityType} extract`);
}
