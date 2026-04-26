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
  AgentDashboard,
  AgentRunDetail,
  AgentRunListResponse,
  AILibraryAgent,
  AILibrarySkill,
  AILibrarySkillFile,
  ChatResponse,
  ChatSession,
  ChatSessionWithMessages,
  CreateAgentPayload,
  CreateChatSessionPayload,
  CreateSkillPayload,
  UsageAggregate,
  UsageScope,
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

  /**
   * Resume a paused agent by clearing `paused_reason`. Works for both manual
   * and budget pauses. If monthly spend is still over budget, the sweeper
   * will re-pause within ~60 s — callers should raise the budget first to
   * avoid the flap. 400 when the agent isn't paused.
   */
  async resumeAgent(slug: string): Promise<AILibraryAgent> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/resume`,
      {
        method: 'POST',
        headers: await getAuthHeaders(),
      },
    );
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

  async createSkill(payload: CreateSkillPayload): Promise<AILibrarySkill> {
    const resp = await fetch(`${base()}/skills`, {
      method: 'POST',
      headers: {
        ...(await getAuthHeaders()),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    return handle<AILibrarySkill>(resp);
  },

  /**
   * Delete a skill by slug. Returns void on 204 success.
   *
   * Authorization is enforced server-side: user-owned skills can only be
   * deleted by their creator; system-preset skills require admin. A
   * non-authorized request will reject with a 403 error message.
   */
  async deleteSkill(slug: string): Promise<void> {
    const resp = await fetch(`${base()}/skills/${encodeURIComponent(slug)}`, {
      method: 'DELETE',
      headers: await getAuthHeaders(),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`${resp.status}: ${text}`);
    }
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

  // ─── Agent runs (telemetry) ────────────────────────────────────────────────

  /**
   * List runs for a given agent, newest first. Scoped server-side to the
   * authenticated user. `limit` 1..200, `offset` >= 0.
   */
  async listAgentRuns(
    slug: string,
    limit = 50,
    offset = 0,
  ): Promise<AgentRunListResponse> {
    const qs = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/runs?${qs.toString()}`,
      { headers: await getAuthHeaders() },
    );
    return handle<AgentRunListResponse>(resp);
  },

  /**
   * Per-agent dashboard aggregate — drives the AgentEditor → Dashboard
   * tab. One fat read with: latest run banner, 14-day run activity +
   * success-rate series, tasks by lifecycle status, costs summary,
   * recent tasks, recent runs.
   */
  async getAgentDashboard(slug: string): Promise<AgentDashboard> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/dashboard`,
      { headers: await getAuthHeaders() },
    );
    return handle<AgentDashboard>(resp);
  },

  /** Full detail view of one run. 404 if not owned by the caller. */
  async getRun(runId: string): Promise<AgentRunDetail> {
    const resp = await fetch(`${base()}/runs/${encodeURIComponent(runId)}`, {
      headers: await getAuthHeaders(),
    });
    return handle<AgentRunDetail>(resp);
  },

  /**
   * Request cancellation of a running agent. Server flips `cancel_requested`;
   * the runner observes via RunRecorder polling between tool iterations.
   * Idempotent — 404 only when the run isn't found or isn't still running.
   */
  async cancelRun(runId: string): Promise<void> {
    const resp = await fetch(
      `${base()}/runs/${encodeURIComponent(runId)}/cancel`,
      {
        method: 'POST',
        headers: await getAuthHeaders(),
      },
    );
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`${resp.status}: ${text}`);
    }
  },

  // ─── AI Usage aggregates ───────────────────────────────────────────────────

  /**
   * Monthly rollup per agent for the Usage dashboard. Scope defaults to 'user'
   * (caller's own runs). Team / project scopes require a numeric id and are
   * enforced server-side via RLS on agent_runs.
   *
   * `month` is the calendar month in YYYY-MM format. Aggregation runs in UTC
   * server-side; the backend's _month_bounds() does the parsing.
   */
  async getUsage(
    month: string,
    scope: UsageScope = 'user',
    teamId?: number,
    projectId?: number,
  ): Promise<UsageAggregate> {
    const qs = new URLSearchParams({ month, scope });
    if (scope === 'team' && teamId != null) qs.set('team_id', String(teamId));
    if (scope === 'project' && projectId != null) {
      qs.set('project_id', String(projectId));
    }
    const resp = await fetch(`${base()}/usage?${qs.toString()}`, {
      headers: await getAuthHeaders(),
    });
    return handle<UsageAggregate>(resp);
  },

  // ─── Chat sessions + messages ─────────────────────────────────────────────
  // Pair with backend/app/api/ai_library_router.py chat endpoints (U1).
  // Every chat turn writes an agent_runs row via AgentRunner + RunRecorder;
  // these methods are plain REST wrappers, the telemetry is server-side.

  /**
   * Create a chat session bound to the given agent. Optional
   * project / team / context fields are stored on the session so the
   * Usage dashboard can scope aggregates later.
   */
  async createChatSession(
    slug: string,
    payload: CreateChatSessionPayload = {},
  ): Promise<ChatSession> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/sessions`,
      {
        method: 'POST',
        headers: {
          ...(await getAuthHeaders()),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      },
    );
    return handle<ChatSession>(resp);
  },

  /**
   * List chat sessions for one agent, filtered to the caller's own.
   * Optional ``projectId`` narrows to sessions opened from that project.
   */
  async listChatSessions(
    slug: string,
    projectId?: number,
    limit = 50,
  ): Promise<ChatSession[]> {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (projectId != null) qs.set('project_id', String(projectId));
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/sessions?${qs.toString()}`,
      { headers: await getAuthHeaders() },
    );
    return handle<ChatSession[]>(resp);
  },

  /** Fetch session + full message history. 404 if not owned. */
  async getChatSession(sessionId: string): Promise<ChatSessionWithMessages> {
    const resp = await fetch(
      `${base()}/sessions/${encodeURIComponent(sessionId)}`,
      { headers: await getAuthHeaders() },
    );
    return handle<ChatSessionWithMessages>(resp);
  },

  /** Rename a session. Only the title is user-editable today. */
  async updateChatSession(
    sessionId: string,
    payload: { title?: string },
  ): Promise<ChatSession> {
    const resp = await fetch(
      `${base()}/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: 'PATCH',
        headers: {
          ...(await getAuthHeaders()),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      },
    );
    return handle<ChatSession>(resp);
  },

  /** Soft-delete a session (status='deleted'). 204 on success. */
  async deleteChatSession(sessionId: string): Promise<void> {
    const resp = await fetch(
      `${base()}/sessions/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE', headers: await getAuthHeaders() },
    );
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`${resp.status}: ${text}`);
    }
  },

  /**
   * Send a user turn, get the assistant response.
   *
   * The response contains ``message`` (the persisted assistant message),
   * ``usage`` (this turn's token counts), and ``run_id`` (the agent_runs
   * row that backed this turn — usable to deep-link from the chat bubble
   * to the Runs tab). Returns a 409 if the agent is paused.
   */
  async sendChatMessage(
    sessionId: string,
    content: string,
  ): Promise<ChatResponse> {
    const resp = await fetch(
      `${base()}/sessions/${encodeURIComponent(sessionId)}/chat`,
      {
        method: 'POST',
        headers: {
          ...(await getAuthHeaders()),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content }),
      },
    );
    return handle<ChatResponse>(resp);
  },
};
