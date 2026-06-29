// User-facing agent memory service.
// Talks to /api/v1/agent-memory — see backend agent_memory_router.py.

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

export interface AgentMemoryItem {
  id: number;
  title: string;
  body_md: string;
  kind: string;
  scope: string;
  visibility: string;
  when_to_use: string;
  created_at: string;
  is_owner: boolean;
}

const base = () => `${getApiUrl()}/api/v1/agent-memory`;

export const listAgentMemories = async (): Promise<AgentMemoryItem[]> => {
  const res = await fetch(base(), { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error('Failed to load memories');
  const data = await res.json();
  return data.items ?? [];
};

export const deleteAgentMemory = async (id: number): Promise<void> => {
  const res = await fetch(`${base()}/${id}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error('Failed to delete memory');
};
