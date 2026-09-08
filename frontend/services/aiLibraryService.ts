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
  AgentCapabilities,
  AgentChatPermissions,
  AgentDashboard,
  AgentPermissionAudit,
  AgentRunDetail,
  AgentRunUndoReport,
  AgentUsage,
  AgentRunEvent,
  AgentRunGroupListResponse,
  AgentRunListResponse,
  LiveAgentRun,
  AILibraryAgent,
  AILibraryApprovalRequest,
  AILibraryCommitment,
  AILibraryMCPServer,
  AILibrarySkill,
  AILibraryUsageSummary,
  AILibraryVersionItem,
  AILibrarySkillFile,
  ChatResponse,
  ChatSession,
  ChatSessionWithMessages,
  CreateAgentPayload,
  CreateChatSessionPayload,
  CreateSkillPayload,
  UsageAggregate,
  UsageDailySummary,
  UsageGroupBy,
  UsageRunsPage,
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

/** One attachment sent with a chat turn (shared by /chat and /chat-stream). */
export type ChatAttachmentInput =
  | {
      kind: 'image' | 'video' | 'pdf';
      url?: string;
      data_url?: string;
      mime?: string;
      alt_text?: string;
      /** Temp resource id — persisted with the message for history display. */
      resource_id?: string;
    }
  | {
      kind: 'resource_ref';
      resource_id: string;
      url?: string;
      mime?: string;
      alt_text?: string;
    }
  // P5: a library ASSET reference. Resolved server-side by
  // `asset_ref_resolver` into a consistency prompt plus its primary image,
  // both of which land in `<available_resources>`. `name` rather than
  // `alt_text` — that is the key the backend's asset branch and the reducer's
  // whitelist both read — and `mime` / `url` are present and empty because an
  // asset claims neither a media type nor a fetchable location.
  | {
      kind: 'asset_ref';
      asset_id: string;
      loadout_id: string | null;
      name: string;
      mime?: string;
      url?: string;
    };

/**
 * §5.3: structured selection handle sent alongside a chat turn (shared by
 * /chat and /chat-stream). No quoted text — the selection text is already
 * folded into `content`; this only carries ids so the backend can render a
 * `<user_selection>` instruction block letting the agent target the exact
 * scene/elements instead of re-locating them by content.
 */
export interface ChatScriptContextInput {
  scene_id?: string | null;
  element_ids?: string[];
  element_type?: string | null;
  scene_label?: string | null;
  cross_scene?: boolean;
}

/** Why an agent is unhealthy, plus the one line telling the user what to do. */
export interface AgentFault {
  kind: 'budget' | 'manual' | 'dead_runs';
  detail: string;
}

/** Per-agent rollup served by GET /agents/stats (mig 400 era, spec §B1). */
export interface AgentStatsItem {
  runs_7d: number;
  tokens_7d: number;
  /** Spend over the same window (mig-free, added alongside runs/tokens). */
  cost_cents_7d: number;
  running_count: number;
  needs_input_count: number;
  fault: AgentFault | null;
  /** The agent's last run was cut short by something that is not the agent's
   *  fault (currently only a backend restart mid-run). Deliberately separate
   *  from `fault`: everything keyed off `fault` — red badge, "Faults only"
   *  filter, fault counter — means "this agent needs fixing", and a routine
   *  deploy does not. The copy is written client-side so it can be
   *  translated. */
  interrupted_reason: 'restart' | null;
}

export const aiLibraryService = {
  // ─── Agents ────────────────────────────────────────────────────────────────

  async listAgents(): Promise<AILibraryAgent[]> {
    const resp = await fetch(`${base()}/agents`, {
      headers: await getAuthHeaders(),
    });
    return handle<AILibraryAgent[]>(resp);
  },

  /**
   * Batch stats for every visible agent, keyed by agent id.
   *
   * One request for the whole gallery — the per-agent /dashboard fan-out it
   * replaces cost one round-trip per card.
   */
  async getAgentStats(days = 7): Promise<Record<string, AgentStatsItem>> {
    const resp = await fetch(`${base()}/agents/stats?days=${days}`, {
      headers: await getAuthHeaders(),
    });
    const data = await handle<{ items: Record<string, AgentStatsItem> }>(resp);
    return data.items ?? {};
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

  async updateAgentChatPermissions(
    slug: string,
    perms: AgentChatPermissions,
  ): Promise<AILibraryAgent> {
    return this.updateAgent(slug, { chat_permissions: perms } as Partial<AILibraryAgent>);
  },

  /**
   * Grant/revoke high-risk capabilities and chat perms in ONE PATCH.
   *
   * They live in sibling subtrees of the same `capability_profile` JSONB and
   * the backend deep-merges both without clobbering either, so a single
   * request keeps the permissions tab's Save atomic — two sequential PATCHes
   * could half-apply if the second failed.
   *
   * Omitting a capability field leaves it unchanged; revoking needs an
   * explicit `false` / `'none'`.
   *
   * `permission_change_reason` (2026-08-10 spec §3) is free-text captured
   * into `agent_permission_audits.reason` alongside this PATCH — not an
   * agent content field, so it never lands in `AILibraryAgent`.
   */
  async updateAgentPermissions(
    slug: string,
    perms: {
      chat_permissions?: AgentChatPermissions;
      capabilities?: AgentCapabilities;
      permission_change_reason?: string;
    },
  ): Promise<AILibraryAgent> {
    return this.updateAgent(slug, perms as Partial<AILibraryAgent>);
  },

  /**
   * Most-recent-first audit trail of chat_permissions/capabilities changes
   * for one agent (2026-08-10 spec §3). Gated by the same role check as the
   * write path — 403 for non-owners, 404 for an unknown slug.
   */
  async getPermissionAudits(slug: string): Promise<{ items: AgentPermissionAudit[] }> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/permission-audits`,
      { headers: await getAuthHeaders() },
    );
    return handle<{ items: AgentPermissionAudit[] }>(resp);
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

  /** Hard-delete a user-owned agent. 403 for system presets / non-creators. */
  async deleteAgent(slug: string): Promise<void> {
    const resp = await fetch(`${base()}/agents/${encodeURIComponent(slug)}`, {
      method: 'DELETE',
      headers: await getAuthHeaders(),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`${resp.status}: ${text}`);
    }
  },

  /** Reset a system preset to its defaults — drop the caller's override
   *  layer (mig 341). Returns the refreshed (merged) agent. */
  async deleteAgentOverride(
    slug: string,
    scope: 'user' | 'team' = 'user',
    teamId?: number,
  ): Promise<AILibraryAgent> {
    const qs = new URLSearchParams({ scope });
    if (teamId != null) qs.set('team_id', String(teamId));
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/override?${qs.toString()}`,
      { method: 'DELETE', headers: await getAuthHeaders() },
    );
    return handle<AILibraryAgent>(resp);
  },

  /** Manually pause an agent (paused_reason='manual'). 400 if already paused. */
  async pauseAgent(slug: string): Promise<AILibraryAgent> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/pause`,
      {
        method: 'POST',
        headers: await getAuthHeaders(),
      },
    );
    return handle<AILibraryAgent>(resp);
  },

  /** Derived header-chip status: paused / running / idle (caller-scoped runs). */
  async getAgentStatus(slug: string): Promise<{
    status: 'idle' | 'running' | 'paused';
    paused_reason: string | null;
    running_count: number;
  }> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/status`,
      { headers: await getAuthHeaders() },
    );
    return handle(resp);
  },

  /** Caller's currently-running runs across all agents (Workforce live strip). */
  async getLiveRuns(): Promise<{ items: LiveAgentRun[]; count: number }> {
    const resp = await fetch(`${base()}/runs/live`, {
      headers: await getAuthHeaders(),
    });
    return handle(resp);
  },

  /** Transcript event stream for one run (mig 285). after_seq = incremental poll. */
  async getRunEvents(
    runId: string,
    afterSeq = 0,
  ): Promise<{ items: AgentRunEvent[]; count: number }> {
    const resp = await fetch(
      `${base()}/runs/${encodeURIComponent(runId)}/events?after_seq=${afterSeq}`,
      { headers: await getAuthHeaders() },
    );
    return handle(resp);
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
    conversationId?: string,
  ): Promise<AgentRunListResponse> {
    const qs = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    if (conversationId) qs.set('conversation_id', conversationId);
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/runs?${qs.toString()}`,
      { headers: await getAuthHeaders() },
    );
    return handle<AgentRunListResponse>(resp);
  },

  /**
   * Conversation-grouped runs, newest activity first: one item per chat
   * conversation (turns rolled up) or per standalone run. `total` counts
   * groups. Expand a group's turns via `listAgentRuns(slug, ..., conversationId)`.
   */
  async listAgentRunGroups(
    slug: string,
    limit = 50,
    offset = 0,
  ): Promise<AgentRunGroupListResponse> {
    const qs = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/run-groups?${qs.toString()}`,
      { headers: await getAuthHeaders() },
    );
    return handle<AgentRunGroupListResponse>(resp);
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

  /**
   * "Used by" card data — static consuming-module registry merged with
   * 30d run-trigger counts, conversation bindings, and routine count.
   */
  async getAgentUsage(slug: string): Promise<AgentUsage> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/usage`,
      { headers: await getAuthHeaders() },
    );
    return handle<AgentUsage>(resp);
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

  /**
   * Undo everything a finished run wrote (one-shot; server skips anything a
   * later edit touched and reports it). 409 while the run is still running.
   */
  async undoRun(runId: string): Promise<AgentRunUndoReport> {
    const resp = await fetch(
      `${base()}/runs/${encodeURIComponent(runId)}/undo`,
      { method: 'POST', headers: await getAuthHeaders() },
    );
    return handle<AgentRunUndoReport>(resp);
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
    projectId?: number | string,
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

  /** Caller's own per-call usage rows (last N days), paginated. */
  async getUsageRuns(opts: {
    page?: number;
    pageSize?: number;
    model?: string;
    status?: string;
    days?: number;
    month?: string;
  } = {}): Promise<UsageRunsPage> {
    const qs = new URLSearchParams({
      page: String(opts.page ?? 1),
      page_size: String(opts.pageSize ?? 25),
      days: String(opts.days ?? 30),
    });
    if (opts.month) qs.set('month', opts.month);
    if (opts.model) qs.set('model', opts.model);
    if (opts.status) qs.set('status', opts.status);
    const resp = await fetch(`${base()}/usage/runs?${qs.toString()}`, {
      headers: await getAuthHeaders(),
    });
    return handle<UsageRunsPage>(resp);
  },

  /** Caller's own daily × model rollup (for the usage charts). */
  async getUsageDaily(
    days = 30,
    groupBy: UsageGroupBy = 'model',
    month?: string,
  ): Promise<UsageDailySummary> {
    const qs = new URLSearchParams({
      days: String(days),
      group_by: groupBy,
    });
    if (month) qs.set('month', month);
    const resp = await fetch(`${base()}/usage/daily?${qs.toString()}`, {
      headers: await getAuthHeaders(),
    });
    return handle<UsageDailySummary>(resp);
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
   * Optional ``projectId`` narrows to sessions opened from that project;
   * ``search`` filters by title server-side (whole history, not one page).
   */
  async listChatSessions(
    slug: string,
    projectId?: number | string,
    limit = 50,
    search?: string,
  ): Promise<ChatSession[]> {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (projectId != null) qs.set('project_id', String(projectId));
    if (search) qs.set('search', search);
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/sessions?${qs.toString()}`,
      { headers: await getAuthHeaders() },
    );
    return handle<ChatSession[]>(resp);
  },

  /**
   * Cross-agent session list for the "All sessions" view: every chat
   * session the caller owns, newest first, each carrying ``agent_slug``
   * so the UI can badge and jump to the source agent. ``search`` is a
   * server-side title filter.
   */
  async listAllChatSessions(
    search?: string,
    limit = 50,
  ): Promise<ChatSession[]> {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (search) qs.set('search', search);
    const resp = await fetch(`${base()}/sessions?${qs.toString()}`, {
      headers: await getAuthHeaders(),
    });
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
  /**
   * O4: List the caller's commitments. Optional status filter
   * ('pending' | 'fulfilled' | 'cancelled' | 'failed' | 'expired').
   */
  async listCommitments(options: {
    status?: 'pending' | 'fulfilled' | 'cancelled' | 'failed' | 'expired';
    limit?: number;
  } = {}): Promise<{ items: AILibraryCommitment[]; count: number }> {
    const params = new URLSearchParams();
    if (options.status) params.set('status', options.status);
    if (options.limit !== undefined) params.set('limit', String(options.limit));
    const qs = params.toString();
    const resp = await fetch(
      `${base()}/commitments${qs ? '?' + qs : ''}`,
      { headers: await getAuthHeaders() },
    );
    return handle(resp);
  },

  async fulfillCommitment(commitmentId: number): Promise<{ id: number; status: string }> {
    const resp = await fetch(
      `${base()}/commitments/${commitmentId}/fulfill`,
      { method: 'POST', headers: await getAuthHeaders() },
    );
    return handle(resp);
  },

  async cancelCommitment(commitmentId: number): Promise<{ id: number; status: string }> {
    const resp = await fetch(
      `${base()}/commitments/${commitmentId}/cancel`,
      { method: 'POST', headers: await getAuthHeaders() },
    );
    return handle(resp);
  },

  /**
   * G1+G5 / A: per-user MCP server registrations.
   */
  async listMCPServers(): Promise<{ items: AILibraryMCPServer[]; count: number }> {
    const resp = await fetch(`${base()}/mcp-servers`, {
      headers: await getAuthHeaders(),
    });
    return handle(resp);
  },

  async createMCPServer(input: {
    name: string;
    url: string;
    bearer_token?: string;
    description?: string;
    enabled?: boolean;
  }): Promise<AILibraryMCPServer> {
    const resp = await fetch(`${base()}/mcp-servers`, {
      method: 'POST',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
    return handle(resp);
  },

  async updateMCPServer(
    id: string,
    patch: {
      url?: string;
      bearer_token?: string;
      description?: string;
      enabled?: boolean;
    },
  ): Promise<AILibraryMCPServer> {
    const resp = await fetch(`${base()}/mcp-servers/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    return handle(resp);
  },

  /**
   * B: upload a one-off chat attachment. The file is persisted as a
   * temp resource on the shared library (readable by both the gateway and
   * the worker container that runs issue turns). Returns the resource
   * handle (`resource_id`), its relative `file_path` under DOWNLOAD_PATH,
   * and `url` (a back-compat alias of `file_path`) — pass `kind` + `url`
   * straight into ChatRequest.attachments. Use `resource_id` to promote a
   * temp upload into a long-lived resource later.
   */
  async uploadChatAttachment(file: File): Promise<{
    kind: 'image' | 'video' | 'pdf';
    resource_id: string;
    file_path: string;
    url: string;
    size_bytes: number;
    mime: string | null;
    filename: string;
  }> {
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    const auth = await getAuthHeaders();
    Object.entries(auth).forEach(([k, v]) => {
      // multipart needs browser-set boundary — drop Content-Type
      if (k.toLowerCase() !== 'content-type') headers[k] = v as string;
    });
    const resp = await fetch(`${base()}/chat-attachments/upload`, {
      method: 'POST',
      headers,
      body: formData,
    });
    return handle(resp);
  },

  async deleteMCPServer(id: string): Promise<void> {
    const resp = await fetch(`${base()}/mcp-servers/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: await getAuthHeaders(),
    });
    if (!resp.ok && resp.status !== 204) {
      const text = await resp.text().catch(() => '');
      throw new Error(`${resp.status}: ${text}`);
    }
  },

  /**
   * G1: Approval requests (human-in-loop hook gates).
   */
  async listApprovalRequests(limit = 50): Promise<{
    items: AILibraryApprovalRequest[];
    count: number;
  }> {
    const params = new URLSearchParams({ limit: String(limit) });
    const resp = await fetch(`${base()}/approval-requests?${params}`, {
      headers: await getAuthHeaders(),
    });
    return handle(resp);
  },

  async approveRequest(id: string, note?: string): Promise<{ id: string; status: string }> {
    const resp = await fetch(`${base()}/approval-requests/${encodeURIComponent(id)}/approve`, {
      method: 'POST',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    });
    return handle(resp);
  },

  async rejectRequest(id: string, note?: string): Promise<{ id: string; status: string }> {
    const resp = await fetch(`${base()}/approval-requests/${encodeURIComponent(id)}/reject`, {
      method: 'POST',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    });
    return handle(resp);
  },

  /**
   * Phase 3: Version history (lists snapshots of agents/skills/skill_files).
   */
  async listAgentVersions(slug: string, limit = 50): Promise<{
    items: AILibraryVersionItem[];
    current_version: number | null;
  }> {
    const resp = await fetch(`${base()}/agents/${encodeURIComponent(slug)}/versions?limit=${limit}`, {
      headers: await getAuthHeaders(),
    });
    return handle(resp);
  },

  async getAgentVersion(slug: string, versionNumber: number): Promise<Record<string, unknown>> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/versions/${versionNumber}`,
      { headers: await getAuthHeaders() },
    );
    return handle(resp);
  },

  async getSkillVersion(slug: string, versionNumber: number): Promise<Record<string, unknown>> {
    const resp = await fetch(
      `${base()}/skills/${encodeURIComponent(slug)}/versions/${versionNumber}`,
      { headers: await getAuthHeaders() },
    );
    return handle(resp);
  },

  async rollbackAgent(slug: string, versionNumber: number): Promise<Record<string, unknown>> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/rollback/${versionNumber}`,
      { method: 'POST', headers: await getAuthHeaders() },
    );
    return handle(resp);
  },

  async rollbackSkill(slug: string, versionNumber: number): Promise<Record<string, unknown>> {
    const resp = await fetch(
      `${base()}/skills/${encodeURIComponent(slug)}/rollback/${versionNumber}`,
      { method: 'POST', headers: await getAuthHeaders() },
    );
    return handle(resp);
  },

  /**
   * Phase 3: Per-user token-cost summary for the Billing dashboard.
   */
  async getUsageSummary(days = 30): Promise<AILibraryUsageSummary> {
    const resp = await fetch(`${base()}/usage/summary?days=${days}`, {
      headers: await getAuthHeaders(),
    });
    return handle(resp);
  },

  async listSkillVersions(slug: string, limit = 50): Promise<{
    items: AILibraryVersionItem[];
    current_version: number | null;
  }> {
    const resp = await fetch(`${base()}/skills/${encodeURIComponent(slug)}/versions?limit=${limit}`, {
      headers: await getAuthHeaders(),
    });
    return handle(resp);
  },

  async sendChatMessage(
    sessionId: string,
    content: string,
    options: {
      plan_mode?: 'auto' | 'prompt_user' | 'dry_run';
      attachments?: ChatAttachmentInput[];
      script_context?: ChatScriptContextInput;
      /** Phase 2a: answers the assistant's parked typed question (its id). */
      answer_to?: string;
    } = {},
  ): Promise<ChatResponse> {
    const body: Record<string, unknown> = { content };
    if (options.plan_mode) body.plan_mode = options.plan_mode;
    if (options.answer_to) body.answer_to = options.answer_to;
    if (options.attachments && options.attachments.length > 0) {
      body.attachments = options.attachments;
    }
    if (options.script_context) body.script_context = options.script_context;
    const resp = await fetch(
      `${base()}/sessions/${encodeURIComponent(sessionId)}/chat`,
      {
        method: 'POST',
        headers: {
          ...(await getAuthHeaders()),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      },
    );
    return handle<ChatResponse>(resp);
  },

  /**
   * Phase O (O1): SSE streaming variant of sendChatMessage.
   *
   * Returns a per-event async iterator. Backend currently does
   * "buffered call + chunked emit" (Phase L L3) — frontend sees
   * delta events with (text, offset) and a final done event with
   * (message_id, usage, run_id, tool_calls, total_chars).
   *
   * Caller pattern:
   *   for await (const evt of streamChatMessage(sid, "hi")) {
   *     if (evt.type === 'delta') append(evt.data.text);
   *     if (evt.type === 'done') finalize(evt.data);
   *     if (evt.type === 'error') showError(evt.data.error);
   *   }
   *
   * Uses fetch + ReadableStream rather than EventSource because we
   * need POST + auth headers; EventSource only supports GET.
   */
  async *streamChatMessage(
    sessionId: string,
    content: string,
    options: {
      plan_mode?: 'auto' | 'prompt_user' | 'dry_run';
      attachments?: ChatAttachmentInput[];
      script_context?: ChatScriptContextInput;
      signal?: AbortSignal;
      /** Phase 2a: answers the assistant's parked typed question (its id). */
      answer_to?: string;
    } = {},
  ): AsyncGenerator<{ type: string; data: any }> {
    const body: Record<string, unknown> = { content };
    if (options.plan_mode) body.plan_mode = options.plan_mode;
    if (options.answer_to) body.answer_to = options.answer_to;
    if (options.attachments?.length) body.attachments = options.attachments;
    if (options.script_context) body.script_context = options.script_context;
    const resp = await fetch(
      `${base()}/sessions/${encodeURIComponent(sessionId)}/chat-stream`,
      {
        method: 'POST',
        headers: {
          ...(await getAuthHeaders()),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
        signal: options.signal,
      },
    );
    if (!resp.ok || !resp.body) {
      const errText = await resp.text().catch(() => '');
      throw new Error(`stream HTTP ${resp.status}: ${errText.slice(0, 200)}`);
    }
    // Parse SSE: events separated by \n\n; each event has "event: NAME\n" + "data: JSON\n"
    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // Drain complete events
        let nl;
        while ((nl = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, nl);
          buffer = buffer.slice(nl + 2);
          let evtName = 'message';
          let dataPayload = '';
          for (const line of block.split('\n')) {
            if (line.startsWith('event:')) evtName = line.slice(6).trim();
            else if (line.startsWith('data:')) dataPayload += line.slice(5).trim();
          }
          let data: any = {};
          try {
            data = dataPayload ? JSON.parse(dataPayload) : {};
          } catch {
            data = { _raw: dataPayload };
          }
          yield { type: evtName, data };
          if (evtName === 'done' || evtName === 'error') return;
        }
      }
    } finally {
      try { await reader.cancel(); } catch { /* ignore */ }
    }
  },
};
