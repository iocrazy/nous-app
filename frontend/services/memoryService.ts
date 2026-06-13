// User-facing AI memory management (Claude-style controls).
// Talks to /api/v1/ai/memory/* — see backend ai_memory_router.py.

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface MemoryObservation {
  id: string;
  content: string;
  created_at?: string | null;
  session_id?: string | null;
}

export interface MemoryProfile {
  workspace: string;
  service_available: boolean;
  learn_enabled: boolean;
  inject_enabled: boolean;
  card: string[] | null;
  observations: MemoryObservation[];
}

const base = () => `${getApiUrl()}/api/v1/ai/memory`;

const wsParam = (workspace?: string) =>
  workspace && workspace !== 'default' ? `?workspace=${encodeURIComponent(workspace)}` : '';

async function unwrap<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

export const getMemoryProfile = async (workspace?: string): Promise<MemoryProfile> => {
  const response = await fetch(`${base()}/profile${wsParam(workspace)}`, {
    headers: await getAuthHeaders(),
  });
  return unwrap<MemoryProfile>(response);
};

export const setMemoryPrefs = async (prefs: {
  learn_enabled?: boolean;
  inject_enabled?: boolean;
}): Promise<{ learn_enabled: boolean; inject_enabled: boolean }> => {
  const response = await fetch(`${base()}/prefs`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify(prefs),
  });
  return unwrap(response);
};

export const setMemoryCard = async (
  lines: string[],
  workspace?: string,
): Promise<{ saved: boolean; lines: string[] }> => {
  const response = await fetch(`${base()}/card${wsParam(workspace)}`, {
    method: 'PUT',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ lines }),
  });
  return unwrap(response);
};

export const deleteMemoryObservation = async (
  id: string,
  workspace?: string,
): Promise<void> => {
  const response = await fetch(
    `${base()}/observations/${encodeURIComponent(id)}${wsParam(workspace)}`,
    { method: 'DELETE', headers: await getAuthHeaders() },
  );
  await unwrap(response);
};

export const forgetAllMemory = async (
  workspace?: string,
): Promise<{ deleted: number }> => {
  const response = await fetch(`${base()}${wsParam(workspace)}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  return unwrap(response);
};
