import { Library } from '../types';
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export async function fetchLibraries(scopeId: string): Promise<Library[]> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({ scope_id: scopeId });
  const response = await fetch(`${apiUrl}/api/v1/libraries?${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to fetch libraries');
  const json = await response.json();
  return json.data || [];
}

export async function createLibrary(data: {
  name: string;
  scope_id: string;
  icon?: string;
  color?: string;
}): Promise<Library> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/libraries`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ ...data, scope_type: 'team' }),
  });
  if (!response.ok) throw new Error('Failed to create library');
  const json = await response.json();
  return json.data;
}

export async function updateLibrary(
  libraryId: string,
  data: { name?: string; icon?: string; color?: string; sort_order?: number }
): Promise<Library> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/libraries/${libraryId}`, {
    method: 'PATCH',
    headers: await getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to update library');
  const json = await response.json();
  return json.data;
}

export async function deleteLibrary(libraryId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/libraries/${libraryId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to delete library');
}
