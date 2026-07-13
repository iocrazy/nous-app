// frontend/services/charactersService.ts
//
// Project character library REST client (mig 357, character canvas epic).
// The authored rows the bible cards and the character canvas bind to —
// distinct from the read-only script-derived entities (fetchProjectEntities).

import { apiFetch, ApiError } from './apiClient';

export type CharacterRoleTag = '' | 'lead' | 'support' | 'antagonist';

export interface ProjectCharacter {
  id: string;
  project_id: string;
  name: string;
  role_tag: CharacterRoleTag;
  description: string;
  /** Grouped chips: {"personality": [...], "conflict": [...], ...} */
  tags: Record<string, string[]>;
  portrait_url: string | null;
  source: 'manual' | 'script';
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface CharacterCreatePayload {
  name: string;
  role_tag?: CharacterRoleTag;
  description?: string;
  tags?: Record<string, string[]>;
  portrait_url?: string | null;
}

export type CharacterUpdatePayload = Partial<CharacterCreatePayload> & {
  sort_order?: number;
};

async function readData<T>(response: Response, what: string): Promise<T> {
  const body = (await response.json()) as { success: boolean; data?: T };
  if (!body.success || body.data === undefined) {
    throw new ApiError(`${what} response missing data`, 500);
  }
  return body.data;
}

export async function listCharacters(projectId: string): Promise<ProjectCharacter[]> {
  const response = await apiFetch(`/api/v1/projects/${projectId}/characters`);
  return readData(response, 'characters');
}

export async function createCharacter(
  projectId: string,
  payload: CharacterCreatePayload,
): Promise<ProjectCharacter> {
  const response = await apiFetch(`/api/v1/projects/${projectId}/characters`, {
    method: 'POST',
    json: payload,
  });
  return readData(response, 'character create');
}

export async function updateCharacter(
  projectId: string,
  characterId: string,
  payload: CharacterUpdatePayload,
): Promise<ProjectCharacter> {
  const response = await apiFetch(
    `/api/v1/projects/${projectId}/characters/${characterId}`,
    { method: 'PATCH', json: payload },
  );
  return readData(response, 'character update');
}

export async function deleteCharacter(
  projectId: string,
  characterId: string,
): Promise<void> {
  await apiFetch(`/api/v1/projects/${projectId}/characters/${characterId}`, {
    method: 'DELETE',
  });
}

/** Materialize script-derived character names into authored rows (idempotent —
 *  re-running never clobbers curated rows). Returns the full library. */
export async function extractCharactersFromScript(
  projectId: string,
): Promise<ProjectCharacter[]> {
  const response = await apiFetch(`/api/v1/projects/${projectId}/characters/extract`, {
    method: 'POST',
  });
  return readData(response, 'character extract');
}
