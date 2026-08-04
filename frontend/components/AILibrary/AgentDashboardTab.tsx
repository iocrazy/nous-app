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
import { useNavigate, useParams } from 'react-router-dom';
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
  Briefcase,
  CalendarClock,
  CheckCircle2,
  CircleDashed,
  ExternalLink,
  FolderOpen,
  Lightbulb,
  ListTodo,
  MessageSquare,
  Palette,
  Plug,
} from 'lucide-react';

import { aiLibraryService } from '../../services/aiLibraryService';
import type { AgentDashboard, AgentUsage } from '../../types';

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

export function formatCost(cents: number): string {
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
      return 'bg-[var(--accent-soft)] text-[var(--accent-text)] border-[var(--accent-border)]';
    case 'cancelled':
      return 'bg-ink-500/15 text-ink-300 border-ink-500/30';
    case 'queued':
    case 'waiting_for_other':
    case 'blocked':
      return 'bg-amber-500/15 text-warn border-amber-500/30';
    default:
      return 'bg-ink-500/15 text-ink-300 border-ink-500/30';
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
// Palette anchors (warm-paper-palette, K1 remap): green = terminal-success,
// plum (agent hue) = in-flight/agent-owned, ochre = waiting/warn, brick =
// danger/failed. `cancelled` stays a literal neutral gray — it's genuinely
// a non-accent status, not a black/gray standing in for emphasis.
export const STATUS_COLORS: Record<string, string> = {
  done: '#1E7A5B',
  completed: '#1E7A5B',
  in_progress: '#7A5E8F',
  assigned: '#AF9BBF',
  queued: '#A87B2B',
  waiting_for_other: '#A87B2B',
  blocked: '#A87B2B',
  failed: '#AD5147',
  cancelled: '#a1a1aa',
};

function statusColor(status: string): string {
  return STATUS_COLORS[status] ?? '#71717a';
}

// "Used by" card — icon per consuming module (module_key = team route segment).
const MODULE_ICONS: Record<string, typeof FolderOpen> = {
  resources: FolderOpen,
  projects: Briefcase,
  canvas: Palette,
  parser: Lightbulb,
  issues: ListTodo,
};

/**
 * Static registry chips + 30d dynamic evidence for one agent.
 * Fetched once (registry data doesn't change mid-session).
 */
function UsageCard({ slug }: { slug: string }): React.ReactElement | null {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();
  const [usage, setUsage] = useState<AgentUsage | null>(null);

  useEffect(() => {
    let cancelled = false;
    aiLibraryService
      .getAgentUsage(slug)
      .then((u) => {
        if (!cancelled) setUsage(u);
      })
      .catch((err) => {
        console.error('[UsageCard] fetch failed:', err);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  if (!usage) return null;

  const runTotal = usage.trigger_counts.reduce((s, tr) => s + tr.count, 0);
  const featureCounts = usage.trigger_counts.reduce<Record<string, number>>(
    (acc, tr) => {
      acc[tr.feature_key] = (acc[tr.feature_key] ?? 0) + tr.count;
      return acc;
    },
    {},
  );
  const hasAnything =
    usage.modules.length > 0 ||
    runTotal > 0 ||
    usage.conversation_count > 0 ||
    usage.routine_count > 0;

  return (
    <section>
      <h3 className="text-sm font-medium text-ink-200 mb-2">
        {t('aiLibrary.agents.usage.title', 'Used By')}
      </h3>
      <div className="rounded-lg border border-ink-800 bg-ink-900/40 p-3">
        {hasAnything ? (
          <div className="space-y-2.5">
            {usage.modules.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {usage.modules.map((m) => {
                  const Icon = MODULE_ICONS[m.module_key] ?? Plug;
                  const label = t(
                    `aiLibrary.agents.usage.module.${m.module_key}`,
                    m.module_key,
                  );
                  const feature = t(
                    `aiLibrary.agents.usage.feature.${m.feature_key}`,
                    m.feature_key,
                  );
                  return (
                    <button
                      key={`${m.module_key}:${m.feature_key}`}
                      type="button"
                      disabled={!teamId}
                      onClick={() =>
                        teamId && navigate(`/team/${teamId}/${m.module_key}`)
                      }
                      className="inline-flex items-center gap-1.5 rounded-md border border-ink-700 bg-ink-800/60 px-2.5 py-1 text-xs text-ink-200 hover:border-[var(--accent-border)] hover:text-[var(--accent-text)] disabled:cursor-default disabled:hover:border-ink-700 disabled:hover:text-ink-200 transition-colors"
                      title={feature}
                    >
                      <Icon size={12} />
                      <span>{label}</span>
                      <span className="text-ink-500">· {feature}</span>
                    </button>
                  );
                })}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-500">
              <span>
                {t('aiLibrary.agents.usage.runs30d', '{{count}} runs in {{days}}d', {
                  count: runTotal,
                  days: usage.window_days,
                })}
                {runTotal > 0 && (
                  <span className="text-ink-600">
                    {' '}
                    (
                    {Object.entries(featureCounts)
                      .sort((a, b) => b[1] - a[1])
                      .map(
                        ([fk, c]) =>
                          `${t(`aiLibrary.agents.usage.feature.${fk}`, fk)} ${c}`,
                      )
                      .join(' · ')}
                    )
                  </span>
                )}
              </span>
              <span className="inline-flex items-center gap-1">
                <MessageSquare size={11} />
                {t('aiLibrary.agents.usage.conversations', '{{count}} conversations', {
                  count: usage.conversation_count,
                })}
              </span>
              <span className="inline-flex items-center gap-1">
                <CalendarClock size={11} />
                {t('aiLibrary.agents.usage.routines', '{{count}} routines', {
                  count: usage.routine_count,
                })}
              </span>
            </div>
          </div>
        ) : (
          <div className="text-xs text-ink-500">
            {t(
              'aiLibrary.agents.usage.empty',
              'No module uses this agent yet — invoke it from chat or bind it to a workflow.',
            )}
          </div>
        )}
      </div>
    </section>
  );
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
      <div className="text-sm text-ink-500 px-1 py-4">
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
      {/* Which product modules use this agent (static registry + 30d evidence) */}
      <UsageCard slug={slug} />

      {/* Latest Run banner */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-ink-200">
            {t('aiLibrary.agents.dashboard.latestRun', 'Latest Run')}
          </h3>
          {latest_run && onOpenRuns && (
            <button
              type="button"
              onClick={onOpenRuns}
              className="text-xs text-[var(--accent-text)] hover:text-[var(--accent-text)] inline-flex items-center gap-0.5"
            >
              {t('aiLibrary.agents.dashboard.viewDetails', 'View details')}
              <ExternalLink size={11} />
            </button>
          )}
        </div>
        {latest_run ? (
          <div className="rounded-lg border border-ink-800 bg-ink-900/40 p-3 space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <StatusBadge status={latest_run.status} />
              <code className="text-[11px] text-ink-400 font-mono">
                {String(latest_run.id).slice(0, 8)}
              </code>
              {latest_run.trigger && (
                <span className="px-1.5 py-0.5 rounded text-[10px] bg-ink-800 text-ink-400">
                  {latest_run.trigger}
                </span>
              )}
              <span className="text-[11px] text-ink-500 ml-auto">
                {formatRelative(latest_run.started_at)}
              </span>
            </div>
            {latest_run.output_summary && (
              <p className="text-xs text-ink-300 leading-relaxed line-clamp-3">
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
          <div className="rounded-lg border border-dashed border-ink-800 bg-ink-900/20 p-3 text-xs text-ink-500 inline-flex items-center gap-2">
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
                cursor={{ fill: 'rgba(30,122,91,0.08)' }}
              />
              <Bar dataKey="count" fill="#1E7A5B" radius={[2, 2, 0, 0]} />
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
                  cursor={{ fill: 'rgba(30,122,91,0.08)' }}
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
                cursor={{ fill: 'rgba(30,122,91,0.08)' }}
              />
              <Legend wrapperStyle={{ fontSize: 10, color: '#a1a1aa' }} />
              <Bar dataKey="success" stackId="r" fill="#1E7A5B" name="success" />
              <Bar dataKey="fail" stackId="r" fill="#AD5147" name="fail" />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </section>

      {/* Costs summary */}
      <section>
        <h3 className="text-sm font-medium text-ink-200 mb-2">
          {t('aiLibrary.agents.dashboard.costs', 'Costs')}{' '}
          <span className="text-[11px] text-ink-500 font-normal">
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
          <h3 className="text-sm font-medium text-ink-200 mb-2">
            {t('aiLibrary.agents.dashboard.recentTasks', 'Recent Tasks')}
          </h3>
          <div className="rounded-lg border border-ink-800 overflow-hidden">
            {recent_tasks.map((task) => (
              <div
                key={task.id}
                className="flex items-center gap-2 px-3 py-2 border-b border-ink-800 last:border-b-0 text-xs"
              >
                <StatusBadge status={task.lifecycle_status} />
                <span className="flex-1 truncate text-ink-300" title={task.title ?? ''}>
                  {task.title || (
                    <span className="italic text-ink-500">
                      {t('aiLibrary.agents.untitledTask')}
                    </span>
                  )}
                </span>
                <span className="text-[11px] text-ink-500">
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
          <h3 className="text-sm font-medium text-ink-200">
            {t('aiLibrary.agents.dashboard.recentRuns', 'Recent Runs')}
          </h3>
          {onOpenRuns && (
            <button
              type="button"
              onClick={onOpenRuns}
              className="text-xs text-[var(--accent-text)] hover:text-[var(--accent-text)]"
            >
              {t('aiLibrary.agents.dashboard.seeAll', 'See all')} →
            </button>
          )}
        </div>
        {recent_runs.length === 0 ? (
          <div className="text-xs text-ink-500 italic">
            {t('aiLibrary.agents.dashboard.noRuns', 'No runs yet.')}
          </div>
        ) : (
          <div className="rounded-lg border border-ink-800 overflow-hidden">
            <table className="w-full text-left text-xs">
              <thead className="bg-ink-900/60 text-ink-500">
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
                  <tr key={run.id} className="border-t border-ink-800">
                    <td className="px-3 py-2 text-ink-400 whitespace-nowrap">
                      {new Date(run.started_at).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-2 font-mono text-ink-300">
                      {String(run.id).slice(0, 8)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink-300">
                      {formatTokens(run.prompt_tokens)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink-300">
                      {formatTokens(run.completion_tokens)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink-300">
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
    <div className="rounded-lg border border-ink-800 bg-ink-900/40 p-3 min-w-0">
      <div className="mb-2">
        <h4 className="text-xs font-medium text-ink-300">{title}</h4>
        {subtitle && <p className="text-[10px] text-ink-500">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

function EmptyChartHint({ text }: { text: string }): React.ReactElement {
  return (
    <div className="h-[120px] flex items-center justify-center text-[11px] text-ink-500 italic">
      {text}
    </div>
  );
}

function CostStat({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900/40 p-3">
      <div className="text-[10px] uppercase tracking-wide text-ink-500">{label}</div>
      <div className="text-base font-medium text-ink-100 tabular-nums mt-0.5">{value}</div>
    </div>
  );
}
