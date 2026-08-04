/**
 * Team AI Usage + Budget panel (W3c).
 *
 * Team-scoped cost observability read from the ai_usage_hourly rollup:
 *   - totals header (tokens + cost)
 *   - stacked-by-day chart, group-by pills (Agent / Model / Module / Project /
 *     Attribution)
 *   - a budget card showing month-spend vs the monthly ceiling, with an edit
 *     affordance (owner/admin)
 *
 * Distinct from the caller-scoped agent analytics on UsagePage — this one
 * answers "what did THIS team burn, and are we within budget?".
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { PageHeader } from '../components/layout/PageHeader';
import { useToast } from '../components/Toast';
import { useWorkspaceScope } from '../hooks/useWorkspaceScope';
import { aiLibraryService } from '../services/aiLibraryService';
import {
  usageService,
  type TeamAiBudget,
  type UsageGroupBy,
  type UsageSummary,
} from '../services/usageService';
import {
  formatCentsAsUsd,
  formatTokens,
  groupLabel,
  pivotDaily,
  presetRange,
  type RangePreset,
} from './usagePanelHelpers';

const RANGE_PRESETS: { key: RangePreset; label: string }[] = [
  { key: '7d', label: '7d' },
  { key: '30d', label: '30d' },
  { key: 'month', label: 'This month' },
];

const GROUP_BYS: { key: UsageGroupBy; label: string }[] = [
  { key: 'agent', label: 'Agent' },
  { key: 'model', label: 'Model' },
  { key: 'module', label: 'Module' },
  { key: 'project', label: 'Project' },
  { key: 'attribution', label: 'Attribution' },
];

const BAR_COLORS = [
  '#6366f1',
  '#22c55e',
  '#f59e0b',
  '#ec4899',
  '#06b6d4',
  '#a855f7',
  '#ef4444',
  '#84cc16',
];

function Pill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1 text-xs rounded-md transition-colors ${
        active
          ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
          : 'text-ink-400 hover:text-ink-200'
      }`}
    >
      {children}
    </button>
  );
}

export function TeamAiUsagePage() {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const { scopeId } = useWorkspaceScope();

  const [preset, setPreset] = useState<RangePreset>('30d');
  const [groupBy, setGroupBy] = useState<UsageGroupBy>('model');
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [budget, setBudget] = useState<TeamAiBudget | null>(null);
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [editingBudget, setEditingBudget] = useState(false);
  const [budgetInput, setBudgetInput] = useState('');

  const range = useMemo(() => presetRange(preset), [preset]);

  const load = useCallback(async () => {
    if (!scopeId) return;
    setLoading(true);
    try {
      const [s, b] = await Promise.all([
        usageService.getSummary(scopeId, {
          from: range.from,
          to: range.to,
          groupBy,
        }),
        usageService.getTeamBudget(scopeId),
      ]);
      setSummary(s);
      setBudget(b);
    } catch (err) {
      console.error('[TeamAiUsagePage] load failed', err);
      addToast(
        `Failed to load usage: ${err instanceof Error ? err.message : 'error'}`,
        'error',
      );
    } finally {
      setLoading(false);
    }
  }, [scopeId, range.from, range.to, groupBy, addToast]);

  useEffect(() => {
    void load();
  }, [load]);

  // Agent-name enrichment only when grouping by agent.
  useEffect(() => {
    if (groupBy !== 'agent' || Object.keys(agentNames).length > 0) return;
    aiLibraryService
      .listAgents()
      .then((agents) => {
        const map: Record<string, string> = {};
        for (const a of agents) map[a.id] = a.name;
        setAgentNames(map);
      })
      .catch((err) => console.error('[TeamAiUsagePage] agent enrich failed', err));
  }, [groupBy, agentNames]);

  const chart = useMemo(() => {
    if (!summary) return { rows: [], keys: [] as string[] };
    return pivotDaily(summary.daily, 'cost_cents');
  }, [summary]);

  const startEditBudget = () => {
    const cents = budget?.monthly_budget_cents;
    setBudgetInput(cents != null ? String(cents / 100) : '');
    setEditingBudget(true);
  };

  const saveBudget = async () => {
    if (!scopeId) return;
    const trimmed = budgetInput.trim();
    const cents = trimmed === '' ? null : Math.round(parseFloat(trimmed) * 100);
    if (cents != null && (Number.isNaN(cents) || cents < 0)) {
      addToast('Enter a non-negative amount (or blank for unlimited)', 'error');
      return;
    }
    try {
      const updated = await usageService.setTeamBudget(scopeId, cents);
      setBudget(updated);
      setEditingBudget(false);
      addToast('Budget saved', 'success');
    } catch (err) {
      addToast(
        `Failed to save budget: ${err instanceof Error ? err.message : 'error'}`,
        'error',
      );
    }
  };

  const total = summary?.total;
  const spendUsd = budget ? budget.month_spend_cents / 100 : 0;
  const budgetUsd =
    budget?.monthly_budget_cents != null ? budget.monthly_budget_cents / 100 : null;
  const pct =
    budgetUsd && budgetUsd > 0 ? Math.min(100, (spendUsd / budgetUsd) * 100) : 0;

  return (
    <div className="pt-6 px-6 pb-16 max-w-5xl mx-auto">
      <PageHeader
        title={t('teamUsage.title', 'AI Cost & Budget')}
        subtitle={t(
          'teamUsage.subtitle',
          'Token spend for this workspace, attributed by agent, model, and human vs automation.',
        )}
      />

      {/* Budget card */}
      <div className="mt-4 rounded-xl border border-ink-800 bg-ink-900/40 p-4">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium text-ink-200">Monthly budget</div>
          {!editingBudget && (
            <button
              type="button"
              onClick={startEditBudget}
              className="text-xs text-[var(--accent-text)] hover:underline"
            >
              Edit
            </button>
          )}
        </div>

        {editingBudget ? (
          <div className="mt-3 flex items-center gap-2">
            <span className="text-ink-400 text-sm">$</span>
            <input
              autoFocus
              value={budgetInput}
              onChange={(e) => setBudgetInput(e.target.value)}
              placeholder="Unlimited"
              className="w-32 bg-ink-950 border border-ink-700 rounded-md px-2 py-1 text-sm text-ink-100"
            />
            <span className="text-ink-500 text-xs">/ month (blank = unlimited)</span>
            <button
              type="button"
              onClick={saveBudget}
              className="ml-2 px-3 py-1 text-xs rounded-md bg-[var(--accent-soft)] text-[var(--accent-text)]"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setEditingBudget(false)}
              className="px-3 py-1 text-xs rounded-md text-ink-400 hover:text-ink-200"
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="mt-3">
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold text-ink-100">
                {formatCentsAsUsd(budget?.month_spend_cents || 0)}
              </span>
              <span className="text-sm text-ink-500">
                {budgetUsd != null
                  ? `of ${formatCentsAsUsd((budgetUsd || 0) * 100)} this month`
                  : 'this month · no budget set'}
              </span>
              {budget?.over_budget && (
                <span className="text-xs font-medium text-red-400 ml-1">
                  Over budget — automation paused
                </span>
              )}
            </div>
            {budgetUsd != null && (
              <div className="mt-2 h-2 w-full rounded-full bg-ink-800 overflow-hidden">
                <div
                  className={`h-full rounded-full ${
                    budget?.over_budget ? 'bg-red-500' : 'bg-[var(--accent)]'
                  }`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Controls */}
      <div className="mt-5 flex flex-wrap items-center gap-3 justify-between">
        <div className="flex items-center gap-1 bg-ink-900/40 rounded-lg p-1">
          {RANGE_PRESETS.map((r) => (
            <Pill key={r.key} active={preset === r.key} onClick={() => setPreset(r.key)}>
              {r.label}
            </Pill>
          ))}
        </div>
        <div className="flex items-center gap-1 bg-ink-900/40 rounded-lg p-1">
          {GROUP_BYS.map((g) => (
            <Pill
              key={g.key}
              active={groupBy === g.key}
              onClick={() => setGroupBy(g.key)}
            >
              {g.label}
            </Pill>
          ))}
        </div>
      </div>

      {/* Totals */}
      <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Total cost" value={formatCentsAsUsd(total?.cost_cents || 0)} />
        <Stat label="Total tokens" value={formatTokens(total?.total_tokens || 0)} />
        <Stat label="Prompt" value={formatTokens(total?.prompt_tokens || 0)} />
        <Stat label="Completion" value={formatTokens(total?.completion_tokens || 0)} />
      </div>

      {/* Chart */}
      <div className="mt-4 rounded-xl border border-ink-800 bg-ink-900/40 p-4">
        <div className="text-xs text-ink-500 mb-2">Daily cost (USD), stacked by {groupBy}</div>
        <div className="h-72 w-full">
          {loading && !summary ? (
            <div className="h-full flex items-center justify-center text-ink-600 text-sm">
              Loading…
            </div>
          ) : chart.rows.length === 0 ? (
            <div className="h-full flex items-center justify-center text-ink-600 text-sm">
              No usage in this range.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart.rows}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                <XAxis
                  dataKey="day"
                  stroke="#a1a1aa"
                  tick={{ fill: '#a1a1aa', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  stroke="#a1a1aa"
                  tick={{ fill: '#a1a1aa', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${Number(v).toFixed(2)}`}
                />
                <Tooltip
                  cursor={{ fill: '#27272a55' }}
                  contentStyle={{
                    backgroundColor: '#18181b',
                    border: '1px solid #3f3f46',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(v: number | string, name: string) => [
                    `$${Number(v).toFixed(2)}`,
                    groupLabel(name === '__none__' ? null : name, groupBy, agentNames),
                  ]}
                  labelStyle={{ color: '#e4e4e7' }}
                />
                <Legend
                  wrapperStyle={{ fontSize: 11 }}
                  formatter={(value) =>
                    groupLabel(value === '__none__' ? null : value, groupBy, agentNames)
                  }
                />
                {chart.keys.map((k, i) => (
                  <Bar
                    key={k}
                    dataKey={k}
                    stackId="cost"
                    fill={BAR_COLORS[i % BAR_COLORS.length]}
                    radius={i === chart.keys.length - 1 ? [3, 3, 0, 0] : [0, 0, 0, 0]}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Group breakdown */}
      <div className="mt-4 rounded-xl border border-ink-800 bg-ink-900/40 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-ink-500 text-xs border-b border-ink-800">
              <th className="px-4 py-2 font-medium">{GROUP_BYS.find((g) => g.key === groupBy)?.label}</th>
              <th className="px-4 py-2 font-medium text-right">Tokens</th>
              <th className="px-4 py-2 font-medium text-right">Cost</th>
              <th className="px-4 py-2 font-medium text-right">Calls</th>
            </tr>
          </thead>
          <tbody>
            {(summary?.groups || []).map((row) => (
              <tr key={row.key ?? '__none__'} className="border-b border-ink-800/50">
                <td className="px-4 py-2 text-ink-200">
                  {groupLabel(row.key, groupBy, agentNames)}
                </td>
                <td className="px-4 py-2 text-right text-ink-300">
                  {formatTokens(row.total_tokens)}
                </td>
                <td className="px-4 py-2 text-right text-ink-300">
                  {formatCentsAsUsd(row.cost_cents)}
                </td>
                <td className="px-4 py-2 text-right text-ink-400">{row.event_count}</td>
              </tr>
            ))}
            {(!summary || summary.groups.length === 0) && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-ink-600">
                  No usage in this range.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900/40 p-3">
      <div className="text-xs text-ink-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-ink-100">{value}</div>
    </div>
  );
}

export default TeamAiUsagePage;
