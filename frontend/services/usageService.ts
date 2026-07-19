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

const base = (): string => `${getApiUrl()}/api/v1`;

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`${resp.status}: ${text}`);
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
}

export interface UsageGroupRow {
  key: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_cents: number;
  event_count: number;
}

export interface UsageDailyRow {
  day: string;
  key: string | null;
  total_tokens: number;
  cost_cents: number;
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
