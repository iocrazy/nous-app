import { Library } from '../types';
import { apiClient } from './apiClient';

interface Envelope<T> {
  data?: T;
}

export async function fetchLibraries(scopeId: string): Promise<Library[]> {
  const response = await apiClient.get<Envelope<Library[]>>('/api/v1/libraries', {
    query: { scope_id: scopeId },
  });
  return response.data || [];
}

export async function createLibrary(data: {
  name: string;
  scope_id: string;
  icon?: string;
  color?: string;
}): Promise<Library> {
  const response = await apiClient.post<Envelope<Library>>('/api/v1/libraries', {
    ...data,
    scope_type: 'team',
  });
  if (!response.data) throw new Error('Empty response from createLibrary');
  return response.data;
}

export async function updateLibrary(
  libraryId: string,
  data: { name?: string; icon?: string; color?: string; sort_order?: number },
): Promise<Library> {
  const response = await apiClient.patch<Envelope<Library>>(
    `/api/v1/libraries/${libraryId}`,
    data,
  );
  if (!response.data) throw new Error('Empty response from updateLibrary');
  return response.data;
}

export async function deleteLibrary(libraryId: string): Promise<void> {
  await apiClient.delete(`/api/v1/libraries/${libraryId}`);
}
