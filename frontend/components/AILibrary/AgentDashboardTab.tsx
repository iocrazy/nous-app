/**
 * AgentDashboardTab — Paperclip-style overview for a single agent.
 *
 * One read against /api/v1/ai-library/agents/:slug/dashboard fans out
 * into:
 *   - Latest Run banner (status pill + summary + "View details" link
 *     into the Runs sub-tab)
 *   - 4 chart cards: Run Activity 14d / Tasks by Status / Success Rate
 *     14d / Costs Summary
 *   - Recent Tasks (5)
 *   - Recent Runs table (10) with token + cost columns
 *
 * Polls every 15s so a long-running agent updates without a reload.
 * Stops polling when the tab is hidden (visibilitychange).
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  ActivitySquare,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  ExternalLink,
} from 'lucide-react';

import { aiLibraryService } from '../../services/aiLibraryService';
import type { AgentDashboard } from '../../types';

const POLL_MS = 15_000;

export interface AgentDashboardTabProps {
  slug: string;
  /** Open the Runs sub-tab when the user clicks "View details" links. */
  onOpenRuns?: () => void;
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return '';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86_400)}d ago`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatCost(cents: number): string {
  // cost_cents is in US cents (1 cent = $0.01). For sub-cent precision
  // (most LLM calls land in 0.0x cents), show 4 decimals.
  if (!cents) return '$0.00';
  const dollars = cents / 100;
  if (dollars < 0.01) return `$${dollars.toFixed(4)}`;
  return `$${dollars.toFixed(2)}`;
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case 'completed':
    case 'done':
      return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
    case 'failed':
    case 'heartbeat_lost':
      return 'bg-red-500/15 text-red-300 border-red-500/30';
    case 'running':
    case 'in_progress':
    case 'assigned':
      return 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30';
    case 'cancelled':
      return 'bg-zinc-500/15 text-zinc-300 border-zinc-500/30';
    case 'queued':
    case 'waiting_for_other':
    case 'blocked':
      return 'bg-amber-500/15 text-amber-300 border-amber-500/30';
    default:
      return 'bg-zinc-500/15 text-zinc-300 border-zinc-500/30';
  }
}

const STATUS_ICONS: Record<string, typeof CheckCircle2> = {
  completed: CheckCircle2,
  done: CheckCircle2,
  failed: AlertTriangle,
  heartbeat_lost: AlertTriangle,
};

function StatusBadge({ status }: { status: string }): React.ReactElement {
  const Icon = STATUS_ICONS[status] ?? CircleDashed;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium border ${statusBadgeClass(status)}`}
    >
      <Icon size={11} />
      {status}
    </span>
  );
}

// Coloring: status buckets use a small palette so the legend ties out
// to the bar colors. Centralised so the same map can be used by tests.
export const STATUS_COLORS: Record<string, string> = {
  done: '#10b981',
  completed: '#10b981',
  in_progress: '#6366f1',
  assigned: '#818cf8',
  queued: '#f59e0b',
  waiting_for_other: '#f59e0b',
  blocked: '#f59e0b',
  failed: '#ef4444',
  cancelled: '#a1a1aa',
};

function statusColor(status: string): string {
  return STATUS_COLORS[status] ?? '#71717a';
}

export function AgentDashboardTab({
  slug,
  onOpenRuns,
}: AgentDashboardTabProps): React.ReactElement {
  const { t } = useTranslation();
  const [data, setData] = useState<AgentDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchOnce = useCallback(async () => {
    try {
      const next = await aiLibraryService.getAgentDashboard(slug);
      setData(next);
      setError(null);
    } catch (err) {
      console.error('[AgentDashboardTab] fetch failed:', err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    setLoading(true);
    void fetchOnce();
  }, [fetchOnce]);

  useEffect(() => {
    let id: number | null = null;
    const start = () => {
      if (id != null) return;
      id = window.setInterval(() => void fetchOnce(), POLL_MS);
    };
    const stop = () => {
      if (id != null) {
        window.clearInterval(id);
        id = null;
      }
    };
    const onVis = () => {
      if (document.hidden) stop();
      else start();
    };
    if (!document.hidden) start();
    document.addEventListener('visibilitychange', onVis);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [fetchOnce]);

  // Derived: status entries for the BarChart. recharts wants array form.
  const statusEntries = useMemo(() => {
    if (!data) return [] as { status: string; count: number }[];
    return Object.entries(data.tasks_by_status_14d)
      .map(([status, count]) => ({ status, count }))
      .sort((a, b) => b.count - a.count);
  }, [data]);

  if (loading && !data) {
    return (
      <div className="text-sm text-zinc-500 px-1 py-4">
        {t('aiLibrary.agents.dashboard.loading', 'Loading dashboard…')}
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="text-sm text-red-400 px-1 py-4">
        {t('aiLibrary.agents.dashboard.error', 'Failed to load dashboard')}: {error}
      </div>
    );
  }

  if (!data) return <></>;

  const { latest_run, run_activity_14d, success_rate_14d, costs_14d, recent_tasks, recent_runs } =
    data;

  return (
    <div className="space-y-6 px-1 pb-6">
      {/* Latest Run banner */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-zinc-200">
            {t('aiLibrary.agents.dashboard.latestRun', 'Latest Run')}
          </h3>
          {latest_run && onOpenRuns && (
            <button
              type="button"
              onClick={onOpenRuns}
              className="text-xs text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-0.5"
            >
              {t('aiLibrary.agents.dashboard.viewDetails', 'View details')}
              <ExternalLink size={11} />
            </button>
          )}
        </div>
        {latest_run ? (
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <StatusBadge status={latest_run.status} />
              <code className="text-[11px] text-zinc-400 font-mono">
                {latest_run.id.slice(0, 8)}
              </code>
              {latest_run.trigger && (
                <span className="px-1.5 py-0.5 rounded text-[10px] bg-zinc-800 text-zinc-400">
                  {latest_run.trigger}
                </span>
              )}
              <span className="text-[11px] text-zinc-500 ml-auto">
                {formatRelative(latest_run.started_at)}
              </span>
            </div>
            {latest_run.output_summary && (
              <p className="text-xs text-zinc-300 leading-relaxed line-clamp-3">
                {latest_run.output_summary}
              </p>
            )}
            {latest_run.error_message && (
              <p className="text-xs text-red-400 leading-relaxed">
                {latest_run.error_code ? `[${latest_run.error_code}] ` : ''}
                {latest_run.error_message}
              </p>
            )}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-zinc-800 bg-zinc-900/20 p-3 text-xs text-zinc-500 inline-flex items-center gap-2">
            <ActivitySquare size={14} />
            {t(
              'aiLibrary.agents.dashboard.noRunsYet',
              'No runs yet — invoke this agent to see activity here.',
            )}
          </div>
        )}
      </section>

      {/* Charts grid */}
      <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        <ChartCard
          title={t('aiLibrary.agents.dashboard.runActivity', 'Run Activity')}
          subtitle={t('aiLibrary.agents.dashboard.last14d', 'Last 14 days')}
        >
          <ResponsiveContainer width="100%" height={120}>
            <BarChart data={run_activity_14d} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: '#71717a', fontSize: 10 }}
                tickFormatter={(v: string) => v.slice(5)}
                interval="preserveStartEnd"
              />
              <YAxis tick={{ fill: '#71717a', fontSize: 10 }} allowDecimals={false} width={20} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#18181b',
                  border: '1px solid #3f3f46',
                  borderRadius: 6,
                  fontSize: 11,
                }}
                cursor={{ fill: 'rgba(99,102,241,0.08)' }}
              />
              <Bar dataKey="count" fill="#10b981" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title={t('aiLibrary.agents.dashboard.tasksByStatus', 'Tasks by Status')}
          subtitle={t('aiLibrary.agents.dashboard.last14d', 'Last 14 days')}
        >
          {statusEntries.length === 0 ? (
            <EmptyChartHint
              text={t(
                'aiLibrary.agents.dashboard.noTasks',
                'No tasks dispatched in this window.',
              )}
            />
          ) : (
            <ResponsiveContainer width="100%" height={120}>
              <BarChart data={statusEntries} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                <XAxis dataKey="status" tick={{ fill: '#71717a', fontSize: 10 }} />
                <YAxis tick={{ fill: '#71717a', fontSize: 10 }} allowDecimals={false} width={20} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#18181b',
                    border: '1px solid #3f3f46',
                    borderRadius: 6,
                    fontSize: 11,
                  }}
                  cursor={{ fill: 'rgba(99,102,241,0.08)' }}
                />
                <Bar dataKey="count" radius={[2, 2, 0, 0]}>
                  {statusEntries.map((e) => (
                    <Cell key={e.status} fill={statusColor(e.status)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard
          title={t('aiLibrary.agents.dashboard.successRate', 'Success Rate')}
          subtitle={t('aiLibrary.agents.dashboard.last14d', 'Last 14 days')}
        >
          <ResponsiveContainer width="100%" height={120}>
            <BarChart
              data={success_rate_14d.map((d) => ({
                ...d,
                fail: Math.max(d.total - d.success, 0),
              }))}
              margin={{ top: 4, right: 4, left: 0, bottom: 0 }}
              stackOffset="sign"
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: '#71717a', fontSize: 10 }}
                tickFormatter={(v: string) => v.slice(5)}
                interval="preserveStartEnd"
              />
              <YAxis tick={{ fill: '#71717a', fontSize: 10 }} allowDecimals={false} width={20} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#18181b',
                  border: '1px solid #3f3f46',
                  borderRadius: 6,
                  fontSize: 11,
                }}
                cursor={{ fill: 'rgba(99,102,241,0.08)' }}
              />
              <Legend wrapperStyle={{ fontSize: 10, color: '#a1a1aa' }} />
              <Bar dataKey="success" stackId="r" fill="#10b981" name="success" />
              <Bar dataKey="fail" stackId="r" fill="#ef4444" name="fail" />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </section>

      {/* Costs summary */}
      <section>
        <h3 className="text-sm font-medium text-zinc-200 mb-2">
          {t('aiLibrary.agents.dashboard.costs', 'Costs')}{' '}
          <span className="text-[11px] text-zinc-500 font-normal">
            {t('aiLibrary.agents.dashboard.last14d', 'Last 14 days')}
          </span>
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <CostStat
            label={t('aiLibrary.agents.dashboard.inputTokens', 'Input tokens')}
            value={formatTokens(costs_14d.prompt_tokens)}
          />
          <CostStat
            label={t('aiLibrary.agents.dashboard.outputTokens', 'Output tokens')}
            value={formatTokens(costs_14d.completion_tokens)}
          />
          <CostStat
            label={t('aiLibrary.agents.dashboard.runs', 'Runs')}
            value={String(costs_14d.run_count)}
          />
          <CostStat
            label={t('aiLibrary.agents.dashboard.totalCost', 'Total cost')}
            value={formatCost(costs_14d.total_cost_cents)}
          />
        </div>
      </section>

      {/* Recent tasks */}
      {recent_tasks.length > 0 && (
        <section>
          <h3 className="text-sm font-medium text-zinc-200 mb-2">
            {t('aiLibrary.agents.dashboard.recentTasks', 'Recent Tasks')}
          </h3>
          <div className="rounded-lg border border-zinc-800 overflow-hidden">
            {recent_tasks.map((task) => (
              <div
                key={task.id}
                className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 last:border-b-0 text-xs"
              >
                <StatusBadge status={task.lifecycle_status} />
                <span className="flex-1 truncate text-zinc-300" title={task.title ?? ''}>
                  {task.title || (
                    <span className="italic text-zinc-500">untitled task</span>
                  )}
                </span>
                <span className="text-[11px] text-zinc-500">
                  {formatRelative(task.created_at)}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recent runs table */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-zinc-200">
            {t('aiLibrary.agents.dashboard.recentRuns', 'Recent Runs')}
          </h3>
          {onOpenRuns && (
            <button
              type="button"
              onClick={onOpenRuns}
              className="text-xs text-indigo-400 hover:text-indigo-300"
            >
              {t('aiLibrary.agents.dashboard.seeAll', 'See all')} →
            </button>
          )}
        </div>
        {recent_runs.length === 0 ? (
          <div className="text-xs text-zinc-500 italic">
            {t('aiLibrary.agents.dashboard.noRuns', 'No runs yet.')}
          </div>
        ) : (
          <div className="rounded-lg border border-zinc-800 overflow-hidden">
            <table className="w-full text-left text-xs">
              <thead className="bg-zinc-900/60 text-zinc-500">
                <tr>
                  <th className="px-3 py-2 font-normal">
                    {t('aiLibrary.agents.dashboard.date', 'Date')}
                  </th>
                  <th className="px-3 py-2 font-normal">
                    {t('aiLibrary.agents.dashboard.run', 'Run')}
                  </th>
                  <th className="px-3 py-2 font-normal text-right">
                    {t('aiLibrary.agents.dashboard.input', 'Input')}
                  </th>
                  <th className="px-3 py-2 font-normal text-right">
                    {t('aiLibrary.agents.dashboard.output', 'Output')}
                  </th>
                  <th className="px-3 py-2 font-normal text-right">
                    {t('aiLibrary.agents.dashboard.cost', 'Cost')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {recent_runs.map((run) => (
                  <tr key={run.id} className="border-t border-zinc-800">
                    <td className="px-3 py-2 text-zinc-400 whitespace-nowrap">
                      {new Date(run.started_at).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-2 font-mono text-zinc-300">
                      {run.id.slice(0, 8)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                      {formatTokens(run.prompt_tokens)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                      {formatTokens(run.completion_tokens)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                      {run.cost_cents != null ? formatCost(run.cost_cents) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

interface ChartCardProps {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}

function ChartCard({ title, subtitle, children }: ChartCardProps): React.ReactElement {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 min-w-0">
      <div className="mb-2">
        <h4 className="text-xs font-medium text-zinc-300">{title}</h4>
        {subtitle && <p className="text-[10px] text-zinc-500">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

function EmptyChartHint({ text }: { text: string }): React.ReactElement {
  return (
    <div className="h-[120px] flex items-center justify-center text-[11px] text-zinc-500 italic">
      {text}
    </div>
  );
}

function CostStat({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="text-base font-medium text-zinc-100 tabular-nums mt-0.5">{value}</div>
    </div>
  );
}
