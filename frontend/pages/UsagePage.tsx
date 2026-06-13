/**
 * AI Usage dashboard — monthly rollup of agent_runs spend.
 *
 * Routes: /usage (personal scope) and /team/:teamId/usage (team scope).
 * Project scope is deferred; the backend supports it but the UI picker
 * would need a project list we don't need for the first pass.
 *
 * What it shows:
 *   - Month picker (prev / current / next) — disabled "next" past current
 *   - Scope switcher (User / Team) — only Team if user is on a team route
 *   - Summary cards: runs / tokens / cost for the selected (month, scope)
 *   - Per-agent breakdown: bar chart of spend + a detailed table with
 *     prompt/completion/total tokens, cost, failure count
 *
 * Polling: none. The backend query is heavy (aggregation over a full
 * month of agent_runs rows); we re-fetch on month/scope change and
 * provide a Refresh button for live updates.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  DollarSign,
  ListChecks,
  RefreshCw,
  Zap,
} from 'lucide-react';
import type { UsageAggregate, UsagePerAgent, UsageScope } from '../types';
import { aiLibraryService } from '../services/aiLibraryService';
import { useToast } from '../components/Toast';

const AGENT_BAR_COLORS = [
  '#6366f1',
  '#8b5cf6',
  '#ec4899',
  '#06b6d4',
  '#10b981',
  '#f59e0b',
  '#ef4444',
  '#3b82f6',
];

export const UsagePage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const params = useParams();
  const teamIdParam = params.teamId;
  const teamId = teamIdParam ? Number(teamIdParam) : null;
  // Team route → scope defaults to 'team'; personal route → 'user'.
  const defaultScope: UsageScope = teamId != null ? 'team' : 'user';

  const [scope, setScope] = useState<UsageScope>(defaultScope);
  const [month, setMonth] = useState<string>(() => formatMonth(new Date()));
  const [data, setData] = useState<UsageAggregate | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset scope when route team context changes (user navigated to a
  // different team while on the page).
  useEffect(() => {
    setScope(defaultScope);
  }, [defaultScope]);

  const fetchUsage = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const resp = await aiLibraryService.getUsage(
        month,
        scope,
        scope === 'team' && teamId != null ? teamId : undefined,
      );
      setData(resp);
    } catch (err) {
      console.error('[UsagePage] getUsage failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addToast(`Failed to load usage: ${msg}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [month, scope, teamId, addToast]);

  useEffect(() => {
    void fetchUsage();
  }, [fetchUsage]);

  const currentMonthStr = useMemo(() => formatMonth(new Date()), []);
  const isCurrentMonth = month === currentMonthStr;

  const shiftMonth = (delta: -1 | 1): void => {
    const [y, m] = month.split('-').map(Number);
    const d = new Date(Date.UTC(y, m - 1 + delta, 1));
    setMonth(formatMonth(d));
  };

  const perAgent = data?.per_agent ?? [];

  return (
    <div className="max-w-6xl mx-auto space-y-6 p-6 animate-in fade-in slide-in-from-bottom-4 duration-300">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-ink-100">
            {t('aiUsage.title', 'AI Usage')}
          </h1>
          <p className="mt-1 text-sm text-ink-400">
            {t(
              'aiUsage.subtitle',
              'Monthly rollup of token spend and cost across your agents. Data is aggregated server-side from agent_runs.',
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <MonthPicker
            month={month}
            onPrev={() => shiftMonth(-1)}
            onNext={() => shiftMonth(1)}
            isCurrentMonth={isCurrentMonth}
          />
          <button
            type="button"
            onClick={fetchUsage}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm font-medium text-ink-200 hover:bg-ink-700 disabled:opacity-50"
            title={t('aiUsage.refresh', 'Refresh')}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            {t('aiUsage.refresh', 'Refresh')}
          </button>
        </div>
      </header>

      {teamId != null && (
        <ScopeSwitcher
          scope={scope}
          onChange={setScope}
          hasTeam={teamId != null}
        />
      )}

      {error && !data && (
        <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
          <div>
            <div className="font-medium">
              {t('aiUsage.errorTitle', 'Failed to load usage')}
            </div>
            <p className="mt-0.5 opacity-90">{error}</p>
          </div>
        </div>
      )}

      {loading && !data && (
        <p className="text-sm text-ink-500">
          {t('aiUsage.loading', 'Loading usage...')}
        </p>
      )}

      {data && (
        <>
          <SummaryCards data={data} />

          {perAgent.length === 0 ? (
            <div className="rounded-lg border border-dashed border-ink-800 bg-ink-900/40 px-4 py-10 text-center text-sm text-ink-500">
              {t(
                'aiUsage.empty',
                'No agent activity this month. Invocation history will show up here as runs land.',
              )}
            </div>
          ) : (
            <>
              <PerAgentChart perAgent={perAgent} />
              <PerAgentTable perAgent={perAgent} />
            </>
          )}
        </>
      )}
    </div>
  );
};

const MonthPicker: React.FC<{
  month: string;
  onPrev: () => void;
  onNext: () => void;
  isCurrentMonth: boolean;
}> = ({ month, onPrev, onNext, isCurrentMonth }) => {
  const { t } = useTranslation();
  return (
    <div className="inline-flex items-center gap-1 rounded-md border border-ink-700 bg-ink-800 px-1 py-1">
      <button
        type="button"
        onClick={onPrev}
        className="rounded p-1.5 text-ink-300 hover:bg-ink-700"
        aria-label={t('aiUsage.prevMonth', 'Previous month')}
      >
        <ChevronLeft size={14} />
      </button>
      <span className="px-2 text-sm font-medium tabular-nums text-ink-100">
        {month}
      </span>
      <button
        type="button"
        onClick={onNext}
        disabled={isCurrentMonth}
        className="rounded p-1.5 text-ink-300 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
        aria-label={t('aiUsage.nextMonth', 'Next month')}
      >
        <ChevronRight size={14} />
      </button>
    </div>
  );
};

const ScopeSwitcher: React.FC<{
  scope: UsageScope;
  onChange: (s: UsageScope) => void;
  hasTeam: boolean;
}> = ({ scope, onChange, hasTeam }) => {
  const { t } = useTranslation();
  return (
    <div className="inline-flex items-center rounded-md border border-ink-700 bg-ink-800 p-1">
      <ScopeButton
        label={t('aiUsage.scopeUser', 'My usage')}
        active={scope === 'user'}
        onClick={() => onChange('user')}
      />
      {hasTeam && (
        <ScopeButton
          label={t('aiUsage.scopeTeam', 'Team usage')}
          active={scope === 'team'}
          onClick={() => onChange('team')}
        />
      )}
    </div>
  );
};

const ScopeButton: React.FC<{
  label: string;
  active: boolean;
  onClick: () => void;
}> = ({ label, active, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
      active
        ? 'bg-indigo-500/15 text-indigo-300'
        : 'text-ink-400 hover:text-ink-200'
    }`}
  >
    {label}
  </button>
);

const SummaryCards: React.FC<{ data: UsageAggregate }> = ({ data }) => {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <SummaryCard
        icon={<ListChecks size={18} />}
        tint="text-indigo-300 bg-indigo-500/10"
        label={t('aiUsage.totalRuns', 'Total runs')}
        value={data.total_runs.toLocaleString()}
      />
      <SummaryCard
        icon={<Zap size={18} />}
        tint="text-emerald-300 bg-emerald-500/10"
        label={t('aiUsage.totalTokens', 'Total tokens')}
        value={formatTokens(data.total_tokens)}
      />
      <SummaryCard
        icon={<DollarSign size={18} />}
        tint="text-amber-300 bg-amber-500/10"
        label={t('aiUsage.totalCost', 'Total cost')}
        value={formatCost(data.total_cost_cents)}
      />
    </div>
  );
};

const SummaryCard: React.FC<{
  icon: React.ReactNode;
  tint: string;
  label: string;
  value: string;
}> = ({ icon, tint, label, value }) => (
  <div className="rounded-xl border border-ink-800 bg-ink-900/60 p-4">
    <div className="flex items-center gap-3">
      <div className={`rounded-lg p-2 ${tint}`}>{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-medium uppercase tracking-wide text-ink-500">
          {label}
        </div>
        <div className="mt-0.5 text-xl font-semibold text-ink-100 tabular-nums">
          {value}
        </div>
      </div>
    </div>
  </div>
);

const PerAgentChart: React.FC<{ perAgent: UsagePerAgent[] }> = ({ perAgent }) => {
  const { t } = useTranslation();
  // Sort by cost desc so the chart leads with what's actually expensive.
  const sorted = useMemo(
    () =>
      [...perAgent]
        .sort((a, b) => b.cost_cents - a.cost_cents)
        .slice(0, 10)
        .map((row, i) => ({
          name: row.agent_name ?? row.agent_slug ?? row.agent_id.slice(0, 8),
          costCents: Number(row.cost_cents.toFixed(4)),
          tokens: row.total_tokens,
          color: AGENT_BAR_COLORS[i % AGENT_BAR_COLORS.length],
        })),
    [perAgent],
  );

  return (
    <section className="rounded-xl border border-ink-800 bg-ink-900/60 p-5">
      <h3 className="mb-3 text-sm font-semibold text-ink-200">
        {t('aiUsage.chartTitle', 'Spend by agent (top 10)')}
      </h3>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={sorted}>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="#27272a"
              vertical={false}
            />
            <XAxis
              dataKey="name"
              stroke="#a1a1aa"
              tick={{ fill: '#a1a1aa', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              interval={0}
              angle={-20}
              textAnchor="end"
              height={50}
            />
            <YAxis
              stroke="#a1a1aa"
              tick={{ fill: '#a1a1aa', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => formatCost(Number(v))}
            />
            <Tooltip
              cursor={{ fill: '#27272a55' }}
              contentStyle={{
                backgroundColor: '#18181b',
                border: '1px solid #3f3f46',
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(v: number | string) => formatCost(Number(v))}
              labelStyle={{ color: '#e4e4e7' }}
            />
            <Bar dataKey="costCents" radius={[4, 4, 0, 0]}>
              {sorted.map((entry, idx) => (
                <Cell key={entry.name + idx} fill={entry.color} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
};

const PerAgentTable: React.FC<{ perAgent: UsagePerAgent[] }> = ({ perAgent }) => {
  const { t } = useTranslation();
  const sorted = useMemo(
    () => [...perAgent].sort((a, b) => b.cost_cents - a.cost_cents),
    [perAgent],
  );
  return (
    <section className="overflow-hidden rounded-xl border border-ink-800 bg-ink-900/60">
      <div className="border-b border-ink-800 px-5 py-3">
        <h3 className="text-sm font-semibold text-ink-200">
          {t('aiUsage.tableTitle', 'Per-agent breakdown')}
        </h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/80 text-xs font-medium uppercase tracking-wide text-ink-500">
            <tr>
              <th className="px-5 py-2">
                {t('aiUsage.colAgent', 'Agent')}
              </th>
              <th className="px-5 py-2 text-right">
                {t('aiUsage.colRuns', 'Runs')}
              </th>
              <th className="px-5 py-2 text-right">
                {t('aiUsage.colPromptTokens', 'Prompt tokens')}
              </th>
              <th className="px-5 py-2 text-right">
                {t('aiUsage.colCompletionTokens', 'Completion tokens')}
              </th>
              <th className="px-5 py-2 text-right">
                {t('aiUsage.colTotalTokens', 'Total tokens')}
              </th>
              <th className="px-5 py-2 text-right">
                {t('aiUsage.colCost', 'Cost')}
              </th>
              <th className="px-5 py-2 text-right">
                {t('aiUsage.colFailed', 'Failed')}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {sorted.map((row) => (
              <tr
                key={row.agent_id}
                className="transition-colors hover:bg-ink-900"
              >
                <td className="px-5 py-2">
                  <div className="font-medium text-ink-100 truncate">
                    {row.agent_name ?? row.agent_slug ?? row.agent_id.slice(0, 8)}
                  </div>
                  {row.agent_slug && (
                    <div className="font-mono text-xs text-ink-500 truncate">
                      {row.agent_slug}
                    </div>
                  )}
                </td>
                <td className="px-5 py-2 text-right tabular-nums text-ink-300">
                  {row.run_count.toLocaleString()}
                </td>
                <td className="px-5 py-2 text-right tabular-nums text-ink-300">
                  {formatTokens(row.prompt_tokens)}
                </td>
                <td className="px-5 py-2 text-right tabular-nums text-ink-300">
                  {formatTokens(row.completion_tokens)}
                </td>
                <td className="px-5 py-2 text-right tabular-nums text-ink-100 font-medium">
                  {formatTokens(row.total_tokens)}
                </td>
                <td className="px-5 py-2 text-right tabular-nums text-ink-100 font-medium">
                  {formatCost(row.cost_cents)}
                </td>
                <td className="px-5 py-2 text-right tabular-nums">
                  {row.failed_count === 0 ? (
                    <span className="text-ink-500">0</span>
                  ) : (
                    <span className="rounded bg-red-500/10 px-1.5 py-0.5 text-xs font-medium text-red-300">
                      {row.failed_count}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};

// ─── Formatters ────────────────────────────────────────────────────────────

/** YYYY-MM in UTC (matches backend _month_bounds's month parsing). */
function formatMonth(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  return `${y}-${m}`;
}

function formatTokens(n: number): string {
  if (n == null || !Number.isFinite(n)) return '0';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function formatCost(centsFractional: number | null | undefined): string {
  if (centsFractional == null) return '—';
  const cents = Number(centsFractional);
  if (!Number.isFinite(cents)) return '—';
  // Drop noisy three-decimal precision when there's no spend yet — "0.000¢"
  // on the dashboard top card reads as broken data, not zero.
  if (cents === 0) return '0';
  if (cents < 1) return `${cents.toFixed(3)}¢`;
  if (cents < 100) return `${cents.toFixed(2)}¢`;
  return `$${(cents / 100).toFixed(2)}`;
}

export default UsagePage;
