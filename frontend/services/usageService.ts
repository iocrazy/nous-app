/**
 * AI Usage + budget API client (W3c).
 *
 * Talks to the FastAPI backend:
 *   - GET  /api/v1/usage/summary        — team AI spend over a window, grouped
 *   - GET  /api/v1/usage/issues/{id}    — per-issue AI cost
 *   - GET  /api/v1/teams/{id}/ai-budget — team monthly budget + month spend
 *   - PUT  /api/v1/teams/{id}/ai-budget — set the budget (owner/admin)
 *
 * Snowflake ids stay strings end-to-end (never Number()-ed).
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { decodeErrorEnvelope } from './errorEnvelope';

const base = (): string => `${getApiUrl()}/api/v1`;

/**
 * A refusal from one of the usage routes, carrying the code the route TYPED.
 *
 * Production wraps every `HTTPException` in `ErrorResponse`, so the branchable
 * code lives under `details.code` and the envelope's own `code` is only ever
 * `http_<status>` (CLAUDE.md 2026-09-09). `usage_summary_unavailable`,
 * `efficiency_unavailable`, `range_too_long` and `invalid_range` are the ones
 * these routes speak; a caller that has to tell them apart cannot do it from
 * a status line. `message` is for a log, never for the screen — the envelope's
 * sentence is server copy with a request id attached to it.
 */
export class UsageRequestError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(code: string, status: number, message: string) {
    super(message);
    this.name = 'UsageRequestError';
    this.code = code;
    this.status = status;
  }
}

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    // Falls back to `http_<status>` rather than null: a gateway's HTML 502
    // types nothing, and a caller still needs something to switch on.
    let code = `http_${resp.status}`;
    let message = `${resp.status} ${resp.statusText}`;
    try {
      const decoded = decodeErrorEnvelope(await resp.json());
      if (decoded.code) code = decoded.code;
      if (decoded.message) message = decoded.message;
    } catch (err) {
      console.error('[usageService] error body unreadable', err);
    }
    throw new UsageRequestError(code, resp.status, message);
  }
  return resp.json() as Promise<T>;
}

export type UsageGroupBy = 'agent' | 'model' | 'module' | 'project' | 'attribution';

export interface UsageTotals {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_input_tokens: number;
  cost_cents: number;
  event_count: number;
  run_count: number;
  failed_runs: number;
  tool_calls: number;
  tool_errors: number;
  deliverables: number;
  /** 0 件产出时是 null（不知道单价），绝不是 0。 */
  cost_per_deliverable_cents: number | null;
}

export interface UsageGroupRow {
  key: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_cents: number;
  event_count: number;
  run_count: number;
  failed_runs: number;
  tool_calls: number;
  tool_errors: number;
  deliverables: number;
  /** 0 件产出时是 null（不知道单价），绝不是 0。 */
  cost_per_deliverable_cents: number | null;
}

export interface UsageDailyRow {
  day: string;
  key: string | null;
  total_tokens: number;
  cost_cents: number;
  run_count: number;
  failed_runs: number;
  tool_calls: number;
  tool_errors: number;
  deliverables: number;
}

/** 效率读面的分组维度。后端只接这两个（别的是 400 `invalid_group_by`）。 */
export type EfficiencyGroupBy = 'model' | 'agent';

export type EfficiencyScope = 'user' | 'team' | 'project';

export interface EfficiencyGroup {
  key: string;
  label: string;
  run_count: number;
  failed_runs: number;
  /** 没有一个 run 计过时的平均时长是 null，不是 0。 */
  avg_run_ms: number | null;
  tool_calls: number;
  tool_errors: number;
  /** 服务端算好的比率：没调过工具时是 0（确定没错），分母不由前端再猜一次。 */
  tool_error_rate: number;
  deliverables: number;
  cost_cents: number;
  /** 0 件产出时是 null（不知道单价），绝不是 0。 */
  cost_per_deliverable_cents: number | null;
}

export interface EfficiencySummary {
  scope: string;
  /** 回显请求的分组维度——图表不必自己记得问过什么（与 /usage/summary 同契约）。 */
  group_by: EfficiencyGroupBy;
  from: string;
  to: string;
  groups: EfficiencyGroup[];
  /** 开放的表：runner 每加一个终止理由就多一个键，消费方必须容纳没见过的键。 */
  turn_end_reasons: Record<string, number>;
}

export interface UsageSummary {
  team_id: string;
  from: string;
  to: string;
  group_by: UsageGroupBy;
  total: UsageTotals;
  groups: UsageGroupRow[];
  daily: UsageDailyRow[];
}

export interface IssueUsage {
  issue_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_cents: number;
  run_count: number;
}

export interface TeamAiBudget {
  team_id: string;
  monthly_budget_cents: number | null;
  month_spend_cents: number;
  over_budget: boolean;
  updated_by_user_id: string | null;
  updated_at: string | null;
}

export const usageService = {
  async getSummary(
    teamId: string,
    opts: { from?: string; to?: string; groupBy?: UsageGroupBy } = {},
  ): Promise<UsageSummary> {
    const qs = new URLSearchParams({ team_id: teamId });
    if (opts.from) qs.set('from', opts.from);
    if (opts.to) qs.set('to', opts.to);
    qs.set('group_by', opts.groupBy || 'model');
    const resp = await fetch(`${base()}/usage/summary?${qs.toString()}`, {
      headers: await getAuthHeaders(),
    });
    return handle<UsageSummary>(resp);
  },

  /**
   * 一个窗口的运行效率（3c §3.3）：turn_end 分布 + 工具错误率 + 每件产出的花费。
   *
   * 与 `/usage/summary` 是两个端点，因为它们读的是两张表——这一路读 `agent_runs`
   * 的效率五列。窗口 ≤366 天（超出 400 `range_too_long`），`from >= to` 是
   * `invalid_range`；user 之外的 scope 必须带 `id`。
   */
  async getEfficiency(params: {
    scope: EfficiencyScope;
    id?: number;
    from?: string;
    to?: string;
    groupBy?: EfficiencyGroupBy;
  }): Promise<EfficiencySummary> {
    const qs = new URLSearchParams({ scope: params.scope });
    if (params.id != null) qs.set('id', String(params.id));
    if (params.from) qs.set('from', params.from);
    if (params.to) qs.set('to', params.to);
    qs.set('group_by', params.groupBy || 'model');
    const resp = await fetch(
      `${base()}/ai-library/usage/efficiency?${qs.toString()}`,
      { headers: await getAuthHeaders() },
    );
    return handle<EfficiencySummary>(resp);
  },

  async getIssueUsage(issueId: string): Promise<IssueUsage> {
    const resp = await fetch(`${base()}/usage/issues/${issueId}`, {
      headers: await getAuthHeaders(),
    });
    return handle<IssueUsage>(resp);
  },

  async getTeamBudget(teamId: string): Promise<TeamAiBudget> {
    const resp = await fetch(`${base()}/teams/${teamId}/ai-budget`, {
      headers: await getAuthHeaders(),
    });
    return handle<TeamAiBudget>(resp);
  },

  async setTeamBudget(
    teamId: string,
    monthlyBudgetCents: number | null,
  ): Promise<TeamAiBudget> {
    const resp = await fetch(`${base()}/teams/${teamId}/ai-budget`, {
      method: 'PUT',
      headers: await getAuthHeaders(),
      body: JSON.stringify({ monthly_budget_cents: monthlyBudgetCents }),
    });
    return handle<TeamAiBudget>(resp);
  },
};
