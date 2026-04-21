/**
 * AI Library API client.
 *
 * Talks to the FastAPI backend under `/api/v1/ai-library/*`:
 *   - Agents: list / get / update (PATCH)
 *   - Skills: list / get / update (PATCH)
 *   - Skill files: upsert (PUT) / delete
 *
 * Naming: service-level types use the `AILibrary*` prefix to avoid collision
 * with the legacy `AIAgent` in `aiService.ts` and the existing `Skill` in
 * `types.ts`.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import type {
  AILibraryAgent,
  AILibrarySkill,
  AILibrarySkillFile,
  CreateAgentPayload,
} from '../types';

const base = (): string => `${getApiUrl()}/api/v1/ai-library`;

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json() as Promise<T>;
}

/**
 * Encode a skill file path for a FastAPI `{path:path}` param.
 *
 * The `:path` converter accepts slashes, but individual segments must still
 * be URL-encoded (spaces, unicode, `?`, `#`, etc.). Split on `/`, encode each
 * segment, rejoin. Leading slashes are trimmed.
 */
function encodeSkillFilePath(path: string): string {
  return path
    .replace(/^\/+/, '')
    .split('/')
    .map(encodeURIComponent)
    .join('/');
}

export const aiLibraryService = {
  // ─── Agents ────────────────────────────────────────────────────────────────

  async listAgents(): Promise<AILibraryAgent[]> {
    const resp = await fetch(`${base()}/agents`, {
      headers: await getAuthHeaders(),
    });
    return handle<AILibraryAgent[]>(resp);
  },

  async getAgent(slug: string): Promise<AILibraryAgent> {
    const resp = await fetch(`${base()}/agents/${encodeURIComponent(slug)}`, {
      headers: await getAuthHeaders(),
    });
    return handle<AILibraryAgent>(resp);
  },

  async updateAgent(
    slug: string,
    updates: Partial<AILibraryAgent>,
  ): Promise<AILibraryAgent> {
    const resp = await fetch(`${base()}/agents/${encodeURIComponent(slug)}`, {
      method: 'PATCH',
      headers: {
        ...(await getAuthHeaders()),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(updates),
    });
    return handle<AILibraryAgent>(resp);
  },

  async createAgent(payload: CreateAgentPayload): Promise<AILibraryAgent> {
    const resp = await fetch(`${base()}/agents`, {
      method: 'POST',
      headers: {
        ...(await getAuthHeaders()),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    return handle<AILibraryAgent>(resp);
  },

  // ─── Skills ────────────────────────────────────────────────────────────────

  async listSkills(): Promise<AILibrarySkill[]> {
    const resp = await fetch(`${base()}/skills`, {
      headers: await getAuthHeaders(),
    });
    return handle<AILibrarySkill[]>(resp);
  },

  async getSkill(slug: string): Promise<AILibrarySkill> {
    const resp = await fetch(`${base()}/skills/${encodeURIComponent(slug)}`, {
      headers: await getAuthHeaders(),
    });
    return handle<AILibrarySkill>(resp);
  },

  async updateSkill(
    slug: string,
    updates: Partial<AILibrarySkill>,
  ): Promise<AILibrarySkill> {
    const resp = await fetch(`${base()}/skills/${encodeURIComponent(slug)}`, {
      method: 'PATCH',
      headers: {
        ...(await getAuthHeaders()),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(updates),
    });
    return handle<AILibrarySkill>(resp);
  },

  // ─── Skill files ───────────────────────────────────────────────────────────

  async upsertSkillFile(
    slug: string,
    path: string,
    content: string,
    file_type: AILibrarySkillFile['file_type'] = 'markdown',
  ): Promise<AILibrarySkillFile> {
    const resp = await fetch(
      `${base()}/skills/${encodeURIComponent(slug)}/files/${encodeSkillFilePath(path)}`,
      {
        method: 'PUT',
        headers: {
          ...(await getAuthHeaders()),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ path, content, file_type }),
      },
    );
    return handle<AILibrarySkillFile>(resp);
  },

  async deleteSkillFile(slug: string, path: string): Promise<void> {
    const resp = await fetch(
      `${base()}/skills/${encodeURIComponent(slug)}/files/${encodeSkillFilePath(path)}`,
      {
        method: 'DELETE',
        headers: await getAuthHeaders(),
      },
    );
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`${resp.status}: ${text}`);
    }
  },
};
