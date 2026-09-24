/**
 * AI Usage dashboard — console-style usage view over agent_runs.
 *
 * Routes: /usage (personal scope) and /team/:teamId/usage (team scope).
 *
 * User scope (the main view, OpenAI-console shaped):
 *   - One time-range control (7d / 30d / 90d) governs the whole page
 *   - Stat tiles: spend / tokens / requests / success rate
 *   - Hero chart: daily stacked bars, switchable by dimension
 *     (model | agent) and metric (cost | tokens | requests)
 *   - Breakdown table with per-key share bars
 *   - Paginated per-call table
 *
 * Team scope keeps the legacy monthly rollup (month picker + per-agent
 * table): most runs carry no team_id, so a daily/range view would
 * mislead there.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
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
import {
  AlertCircle,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  DollarSign,
  ListChecks,
  Package,
  RefreshCw,
  Zap,
} from 'lucide-react';
import type { UsageDailySummary, UsageGroupBy, UsageRunItem, UsageScope } from '../types';
import type { UsageAggregate, UsagePerAgent } from '../types/api';
import { aiLibraryService } from '../services/aiLibraryService';
import {
  usageService,
  UsageRequestError,
  type EfficiencyGroup,
  type EfficiencySummary,
} from '../services/usageService';
import { useToast } from '../components/Toast';
import { TurnEndBreakdown } from '../components/usage/TurnEndBreakdown';
import { PageHeader } from '../components/layout/PageHeader';
import { usageWindow } from './usagePanelHelpers';

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

const RANGE_PRESETS = [7, 30, 90] as const;

export const UsagePage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const params = useParams();
  const teamIdParam = params.teamId;
  const teamId = teamIdParam ? Number(teamIdParam) : null;
  // Team route → scope defaults to 'team'; personal route → 'user'.
  const defaultScope: UsageScope = teamId != null ? 'team' : 'user';

  const [scope, setScope] = useState<UsageScope>(defaultScope);
  const [days, setDays] = useState<number>(30);
  // 'presets' = rolling last-N-days; 'month' = calendar-month window.
  const [rangeMode, setRangeMode] = useState<'presets' | 'month'>('presets');
  const [groupBy, setGroupBy] = useState<UsageGroupBy>('model');
  const [summary, setSummary] = useState<UsageDailySummary | null>(null);
  // Efficiency is a SECOND request against a second table. It is allowed to
  // fail on its own: the daily rollup already on screen stays true either way,
  // so its failure narrows to two tiles and one typed sentence.
  const [efficiency, setEfficiency] = useState<EfficiencySummary | null>(null);
  const [efficiencyError, setEfficiencyError] = useState<string | null>(null);
  // Team scope keeps the legacy monthly rollup (runs mostly carry no
  // team_id, so a range/daily view would mislead there).
  const [month, setMonth] = useState<string>(() => formatMonth(new Date()));
  const [data, setData] = useState<UsageAggregate | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset scope when route team context changes (user navigated to a
  // different team while on the page).
  useEffect(() => {
    setScope(defaultScope);
  }, [defaultScope]);

  // Bumped by the Refresh button so the runs table reloads with the charts.
  const [refreshKey, setRefreshKey] = useState(0);

  // Request generation. Every control on this page (range, grouping, scope,
  // month) starts a new fetch while the previous one may still be in flight,
  // and the two land in whatever order the network decides. Without a
  // generation check the SLOWER response wins — the user clicks 90d, sees 90d
  // numbers, and then watches them silently revert to the 30d ones while the
  // range pill still reads 90d. Nothing on screen says which window is being
  // shown, so the page is simply wrong and looks fine.
  const reqIdRef = React.useRef(0);

  const fetchUsage = useCallback(async (): Promise<void> => {
    const myReq = ++reqIdRef.current;
    const isCurrent = (): boolean => reqIdRef.current === myReq;
    setLoading(true);
    setError(null);
    setRefreshKey((k) => k + 1);
    try {
      if (scope === 'user') {
        const monthArg = rangeMode === 'month' ? month : undefined;
        const resp = await aiLibraryService.getUsageDaily(days, groupBy, monthArg);
        if (!isCurrent()) return;
        setSummary(resp);
        // 第二个请求：它失败不该把整页打掉，两枚效率格自己退回「—」。
        // 窗口与 summary 同源——不带窗口就是问后端的默认 30 天，于是上面四格和
        // 下面两格说的是两个不同的时间段。
        // 不叫 `window`：那会在这个作用域里遮掉全局的 `window`，下一个在这里写
        // `window.localStorage` 的人会拿到一个 `{from, to}`。
        const win = usageWindow({ days, month: monthArg });
        // 先把上一轮的错误清掉：重试飞行期间该显示「正在加载」，继续挂着上一轮的
        // 错误会让一次正在进行的重试看起来像是又失败了一次。
        setEfficiencyError(null);
        try {
          const eff = await usageService.getEfficiency({
            scope: 'user',
            groupBy,
            from: win.from,
            to: win.to,
          });
          if (!isCurrent()) return;
          setEfficiency(eff);
          setEfficiencyError(null);
        } catch (effErr) {
          // 失败分支同样要过代际判断：旧轮的 503 落地把新轮刚画好的分布条抹掉，
          // 是这类竞态里最难看的一种。
          if (!isCurrent()) return;
          console.error('[UsagePage] efficiency fetch failed:', effErr);
          setEfficiency(null);
          setEfficiencyError(
            effErr instanceof UsageRequestError ? effErr.code : 'unknown',
          );
        }
      } else {
        const resp = await aiLibraryService.getUsage(
          month,
          scope,
          teamId != null ? teamId : undefined,
        );
        if (!isCurrent()) return;
        setData(resp);
      }
    } catch (err) {
      if (!isCurrent()) return;
      console.error('[UsagePage] usage fetch failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addToast(`Failed to load usage: ${msg}`, 'error');
    } finally {
      // 旧轮收尾不许关掉新轮的 spinner。
      if (isCurrent()) setLoading(false);
    }
  }, [scope, days, groupBy, month, rangeMode, teamId, addToast]);

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
    <div className="max-w-6xl space-y-5 pt-6 pb-8 animate-in fade-in slide-in-from-bottom-4 duration-300">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader level="content" title={t('aiUsage.title', 'AI Usage')} className="pb-0" />
        <div className="flex flex-wrap items-center gap-2">
          {teamId != null && (
            <ScopeSwitcher
              scope={scope}
              onChange={setScope}
              hasTeam={teamId != null}
            />
          )}
          {scope === 'user' ? (
            <>
              <div className="inline-flex items-center rounded-md border border-ink-700 bg-ink-800 p-1">
                {RANGE_PRESETS.map((d) => (
                  <button
                    key={d}
                    type="button"
                    onClick={() => {
                      setRangeMode('presets');
                      setDays(d);
                    }}
                    className={`rounded px-2.5 py-1 text-xs font-medium tabular-nums transition-colors ${
                      rangeMode === 'presets' && days === d
                        ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                        : 'text-ink-400 hover:text-ink-200'
                    }`}
                  >
                    {d}d
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => setRangeMode('month')}
                  className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                    rangeMode === 'month'
                      ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                      : 'text-ink-400 hover:text-ink-200'
                  }`}
                >
                  {t('aiUsage.rangeMonth', 'Month')}
                </button>
              </div>
              {rangeMode === 'month' && (
                <MonthPicker
                  month={month}
                  onPrev={() => shiftMonth(-1)}
                  onNext={() => shiftMonth(1)}
                  isCurrentMonth={isCurrentMonth}
                />
              )}
            </>
          ) : (
            <MonthPicker
              month={month}
              onPrev={() => shiftMonth(-1)}
              onNext={() => shiftMonth(1)}
              isCurrentMonth={isCurrentMonth}
            />
          )}
          <button
            type="button"
            onClick={fetchUsage}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-md border border-ink-700 bg-ink-800 px-3 py-1.5 text-xs font-medium text-ink-200 hover:bg-ink-700 disabled:opacity-50"
            title={t('aiUsage.refresh', 'Refresh')}
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            {t('aiUsage.refresh', 'Refresh')}
          </button>
        </div>
      </header>

      {error && (
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

      {loading && !summary && !data && (
        <p className="text-sm text-ink-500">
          {t('aiUsage.loading', 'Loading usage...')}
        </p>
      )}

      {scope === 'user' && summary && (
        <>
          <StatTiles summary={summary} efficiency={efficiency} />
          <HeroChart
            summary={summary}
            groupBy={groupBy}
            onGroupByChange={setGroupBy}
          />
          <section className="rounded-xl border border-ink-800 bg-ink-900/40 p-4">
            <div className="mb-3 text-xs text-ink-500">
              {t('aiUsage.turnEndTitle', 'How turns ended')}
            </div>
            {efficiency ? (
              <TurnEndBreakdown reasons={efficiency.turn_end_reasons ?? {}} />
            ) : efficiencyError ? (
              <EfficiencyNotice code={efficiencyError} />
            ) : (
              // 「还在加载」和「读失败了」共用一个 testid，等于让任何断言失败态的
              // 测试在加载态上也通过。
              <p className="text-xs text-ink-500" data-testid="efficiency-loading">
                {t('aiUsage.efficiencyLoading', 'Loading efficiency...')}
              </p>
            )}
          </section>
          <BreakdownTable summary={summary} />
          <RecentRunsTable
            refreshKey={refreshKey}
            days={days}
            month={rangeMode === 'month' ? month : undefined}
          />
        </>
      )}

      {scope === 'team' && data && (
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
            <PerAgentTable perAgent={perAgent} />
          )}
          <div className="rounded-lg border border-dashed border-ink-800 bg-ink-900/40 px-4 py-6 text-center text-sm text-ink-500">
            {t(
              'aiUsage.detailTeamHint',
              'Per-call detail and daily model charts are available in the My usage view.',
            )}
          </div>
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
        ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
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
        tint="text-[var(--accent-text)] bg-[var(--accent-soft)]"
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
        tint="text-warn bg-amber-500/10"
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
  testId?: string;
}> = ({ icon, tint, label, value, testId }) => (
  <div className="rounded-xl border border-ink-800 bg-ink-900/60 p-4" data-testid={testId}>
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

// ─── User-scope dashboard (range-driven, OpenAI-console style) ─────────────

/**
 * The six-tile header (3c §3.3 / §6 稿三).
 *
 * `success` reads the TURN-END MIX, not run status. `awaiting_input` is a turn
 * that is waiting on a person; counting it as a failure tells the user that
 * asking them a question made things worse (recon C12).
 *
 * The efficiency request is the second one on this page, so `efficiency` is
 * legitimately null for a beat — and while it is, these tiles say «—». Not
 * 100%: «I don't know yet» and «everything succeeded» are not the same news.
 * Same rule for each ratio's denominator: 0 tool calls has no error rate,
 * while 40 calls and 0 errors is a real 0.0%.
 */
export const StatTiles: React.FC<{
  summary: UsageDailySummary;
  efficiency: EfficiencySummary | null;
}> = ({ summary, efficiency }) => {
  const { t } = useTranslation();
  // 按 model / agent 分组时一个窗口常有好几行，逐行求和——只读第一行对多模型用户
  // 永远是错的。
  const sum = (f: (g: EfficiencyGroup) => number): number =>
    efficiency ? efficiency.groups.reduce((a, g) => a + f(g), 0) : 0;
  // 分母是**结束过的回合**，不是全部 run。后端的 reasons 查询带
  // `WHERE turn_end_reason IS NOT NULL`，而 mig 472 不回填存量行——拿
  // `sum(run_count)` 当分母，上线首月这一格会显示个位数，把一个跑得好好的窗口
  // 报成惨败。分布与分母必须同源。字段整个缺席（旧后端 / 半截响应）也落「—」。
  const reasons = efficiency?.turn_end_reasons ?? {};
  const ended = Object.values(reasons).reduce((a, b) => a + (b > 0 ? b : 0), 0);
  const successRate =
    ended > 0
      ? `${(((reasons.completed ?? 0) / ended) * 100).toFixed(0)}%`
      : '—';
  const delivered = sum((g) => g.deliverables);
  const costPerOutput =
    efficiency && delivered > 0
      ? `¢${(sum((g) => g.cost_cents) / delivered).toFixed(2)}`
      : '—';
  const calls = sum((g) => g.tool_calls);
  const toolErrorRate =
    efficiency && calls > 0
      ? `${((sum((g) => g.tool_errors) / calls) * 100).toFixed(1)}%`
      : '—';
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
      <SummaryCard
        icon={<DollarSign size={18} />}
        tint="text-warn bg-amber-500/10"
        label={t('aiUsage.statSpend', 'Spend')}
        value={formatCost(summary.total_cost_cents)}
      />
      <SummaryCard
        icon={<Zap size={18} />}
        tint="text-emerald-300 bg-emerald-500/10"
        label={t('aiUsage.totalTokens', 'Total tokens')}
        value={formatTokens(summary.total_tokens)}
      />
      <SummaryCard
        testId="tile-requests"
        icon={<ListChecks size={18} />}
        tint="text-[var(--accent-text)] bg-[var(--accent-soft)]"
        label={t('aiUsage.statRequests', 'Requests')}
        value={summary.total_requests.toLocaleString()}
      />
      <SummaryCard
        testId="tile-success"
        icon={<AlertCircle size={18} />}
        tint="text-info bg-info-soft"
        label={t('aiUsage.statSuccess', 'Success rate')}
        value={successRate}
      />
      <SummaryCard
        testId="tile-cost-per-output"
        icon={<Package size={18} />}
        tint="text-ok bg-ok-soft"
        label={t('aiUsage.statCostPerOutput', 'Cost / output')}
        value={costPerOutput}
      />
      <SummaryCard
        testId="tile-tool-errors"
        icon={<AlertTriangle size={18} />}
        tint="text-danger bg-danger-soft"
        label={t('aiUsage.statToolErrors', 'Tool error rate')}
        value={toolErrorRate}
      />
    </div>
  );
};

/**
 * What to say when the efficiency read did not come back.
 *
 * One sentence chosen from the code the route typed — never the envelope. A
 * production refusal is `{success, error, code:"http_503", request_id,
 * details:{code}}`; rendering that hands the user a request id and calls it an
 * explanation.
 */
const EfficiencyNotice: React.FC<{ code: string }> = ({ code }) => {
  const { t } = useTranslation();
  const copy = ((): string => {
    switch (code) {
      case 'efficiency_unavailable':
        return t(
          'aiUsage.efficiencyUnavailable',
          'Efficiency stats are unavailable right now. Usage totals above are unaffected.',
        );
      case 'range_too_long':
        return t(
          'aiUsage.efficiencyRangeTooLong',
          'That range is too long for efficiency stats. Pick a window of 366 days or less.',
        );
      case 'invalid_range':
        return t(
          'aiUsage.efficiencyInvalidRange',
          'That range ends before it starts.',
        );
      default:
        return t('aiUsage.efficiencyFailed', 'Efficiency stats could not be loaded.');
    }
  })();
  return (
    <p className="text-xs text-ink-500" data-testid="efficiency-error">
      {copy}
    </p>
  );
};

type DailyMetric = 'cost_cents' | 'total_tokens' | 'requests';

const HeroChart: React.FC<{
  summary: UsageDailySummary;
  groupBy: UsageGroupBy;
  onGroupByChange: (g: UsageGroupBy) => void;
}> = ({ summary, groupBy, onGroupByChange }) => {
  const { t } = useTranslation();
  const [metric, setMetric] = useState<DailyMetric>('total_tokens');

  // Pivot rows → one object per date with a key per series, so Recharts can
  // render one stacked <Bar> per model/agent.
  const { chartData, series } = useMemo(() => {
    const seriesSet = new Set<string>();
    const byDate = new Map<string, Record<string, number | string>>();
    for (const r of summary.daily) {
      const label = r.label || r.key || 'unknown';
      seriesSet.add(label);
      const bucket = byDate.get(r.date) ?? { date: r.date.slice(5) };
      bucket[label] = Number(bucket[label] ?? 0) + Number(r[metric] ?? 0);
      byDate.set(r.date, bucket);
    }
    return { chartData: [...byDate.values()], series: [...seriesSet].sort() };
  }, [summary, metric]);

  const metricLabels: Record<DailyMetric, string> = {
    cost_cents: t('aiUsage.metricCost', 'Cost'),
    total_tokens: t('aiUsage.metricTokens', 'Tokens'),
    requests: t('aiUsage.metricRequests', 'Requests'),
  };

  const fmt = (v: number): string =>
    metric === 'cost_cents'
      ? formatCost(v)
      : metric === 'total_tokens'
        ? formatTokens(v)
        : v.toLocaleString();

  return (
    <section className="rounded-xl border border-ink-800 bg-ink-900/60 p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink-200">
          {t('aiUsage.heroTitle', 'Usage over time')}
        </h3>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex items-center rounded-md border border-ink-700 bg-ink-800 p-0.5">
            <ToggleBtn
              label={t('aiUsage.groupByModel', 'By model')}
              active={groupBy === 'model'}
              onClick={() => onGroupByChange('model')}
            />
            <ToggleBtn
              label={t('aiUsage.groupByAgent', 'By agent')}
              active={groupBy === 'agent'}
              onClick={() => onGroupByChange('agent')}
            />
          </div>
          <div className="inline-flex items-center rounded-md border border-ink-700 bg-ink-800 p-0.5">
            {(Object.keys(metricLabels) as DailyMetric[]).map((m) => (
              <ToggleBtn
                key={m}
                label={metricLabels[m]}
                active={metric === m}
                onClick={() => setMetric(m)}
              />
            ))}
          </div>
        </div>
      </div>
      {chartData.length === 0 ? (
        <div className="flex h-72 items-center justify-center text-sm text-ink-500">
          {t('aiUsage.emptyRange', 'No usage in this range.')}
        </div>
      ) : (
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="#27272a"
                vertical={false}
              />
              <XAxis
                dataKey="date"
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
                tickFormatter={(v) => fmt(Number(v))}
              />
              <Tooltip
                cursor={{ fill: '#27272a55' }}
                contentStyle={{
                  backgroundColor: '#18181b',
                  border: '1px solid #3f3f46',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(v: number | string) => fmt(Number(v))}
                labelStyle={{ color: '#e4e4e7' }}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {series.map((s, i) => (
                <Bar
                  key={s}
                  dataKey={s}
                  stackId="usage"
                  fill={AGENT_BAR_COLORS[i % AGENT_BAR_COLORS.length]}
                  radius={
                    i === series.length - 1 ? [3, 3, 0, 0] : [0, 0, 0, 0]
                  }
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
};

const ToggleBtn: React.FC<{
  label: string;
  active: boolean;
  onClick: () => void;
}> = ({ label, active, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
      active
        ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
        : 'text-ink-400 hover:text-ink-200'
    }`}
  >
    {label}
  </button>
);

/** Per-key rollup table with an inline share bar (console-style breakdown). */
const BreakdownTable: React.FC<{ summary: UsageDailySummary }> = ({
  summary,
}) => {
  const { t } = useTranslation();
  const rows = useMemo(() => {
    const byKey = new Map<
      string,
      {
        label: string;
        requests: number;
        failed: number;
        prompt: number;
        completion: number;
        tokens: number;
        cost: number;
      }
    >();
    for (const r of summary.daily) {
      const label = r.label || r.key || 'unknown';
      const b = byKey.get(label) ?? {
        label,
        requests: 0,
        failed: 0,
        prompt: 0,
        completion: 0,
        tokens: 0,
        cost: 0,
      };
      b.requests += r.requests;
      b.failed += r.failed_requests;
      b.prompt += r.prompt_tokens;
      b.completion += r.completion_tokens;
      b.tokens += r.total_tokens;
      b.cost += r.cost_cents;
      byKey.set(label, b);
    }
    // Share bar keys off cost when anything is priced, else tokens — same
    // fallback logic as the hero metric default.
    const hasCost = [...byKey.values()].some((b) => b.cost > 0);
    const total = [...byKey.values()].reduce(
      (acc, b) => acc + (hasCost ? b.cost : b.tokens),
      0,
    );
    return [...byKey.values()]
      .sort((a, b) => (hasCost ? b.cost - a.cost : b.tokens - a.tokens))
      .map((b, i) => ({
        ...b,
        share: total > 0 ? ((hasCost ? b.cost : b.tokens) / total) * 100 : 0,
        color: AGENT_BAR_COLORS[i % AGENT_BAR_COLORS.length],
      }));
  }, [summary]);

  if (rows.length === 0) return null;

  const dimHeader =
    summary.group_by === 'agent'
      ? t('aiUsage.colAgent', 'Agent')
      : t('aiUsage.colModel', 'Model');

  return (
    <section className="overflow-hidden rounded-xl border border-ink-800 bg-ink-900/60">
      <div className="border-b border-ink-800 px-5 py-3">
        <h3 className="text-sm font-semibold text-ink-200">
          {t('aiUsage.breakdownTitle', 'Breakdown')}
        </h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/80 text-xs font-medium uppercase tracking-wide text-ink-500">
            <tr>
              <th className="px-5 py-2">{dimHeader}</th>
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
              <th className="w-40 px-5 py-2">
                {t('aiUsage.colShare', 'Share')}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {rows.map((row) => (
              <tr key={row.label} className="transition-colors hover:bg-ink-900">
                <td className="px-5 py-2">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2.5 w-2.5 flex-shrink-0 rounded-sm"
                      style={{ backgroundColor: row.color }}
                    />
                    <span className="truncate font-medium text-ink-100">
                      {row.label}
                    </span>
                  </div>
                </td>
                <td className="px-5 py-2 text-right tabular-nums text-ink-300">
                  {row.requests.toLocaleString()}
                </td>
                <td className="px-5 py-2 text-right tabular-nums text-ink-300">
                  {formatTokens(row.prompt)}
                </td>
                <td className="px-5 py-2 text-right tabular-nums text-ink-300">
                  {formatTokens(row.completion)}
                </td>
                <td className="px-5 py-2 text-right tabular-nums font-medium text-ink-100">
                  {formatTokens(row.tokens)}
                </td>
                <td className="px-5 py-2 text-right tabular-nums font-medium text-ink-100">
                  {formatCost(row.cost)}
                </td>
                <td className="px-5 py-2 text-right tabular-nums">
                  {row.failed === 0 ? (
                    <span className="text-ink-500">0</span>
                  ) : (
                    <span className="rounded bg-red-500/10 px-1.5 py-0.5 text-xs font-medium text-red-300">
                      {row.failed}
                    </span>
                  )}
                </td>
                <td className="px-5 py-2">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-800">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.max(row.share, 1)}%`,
                          backgroundColor: row.color,
                        }}
                      />
                    </div>
                    <span className="w-10 text-right text-xs tabular-nums text-ink-400">
                      {row.share.toFixed(0)}%
                    </span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};


const RUNS_PAGE_SIZE = 25;

const RecentRunsTable: React.FC<{
  refreshKey: number;
  days: number;
  month?: string;
}> = ({ refreshKey, days, month }) => {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<UsageRunItem[]>([]);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    let cancelled = false;
    aiLibraryService
      .getUsageRuns({ page, pageSize: RUNS_PAGE_SIZE, days, month })
      .then((resp) => {
        if (cancelled) return;
        setItems(resp.items);
        setTotal(resp.total);
      })
      .catch((err) => {
        console.error('[UsagePage] getUsageRuns failed:', err);
      });
    return () => {
      cancelled = true;
    };
  }, [page, refreshKey, days, month]);

  const from = total === 0 ? 0 : (page - 1) * RUNS_PAGE_SIZE + 1;
  const to = Math.min(page * RUNS_PAGE_SIZE, total);

  return (
    <section className="overflow-hidden rounded-xl border border-ink-800 bg-ink-900/60">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-800 px-5 py-3">
        <h3 className="text-sm font-semibold text-ink-200">
          {month
            ? t('aiUsage.runsTitleMonth', 'Recent calls ({{month}})', { month })
            : t('aiUsage.runsTitleRange', 'Recent calls (last {{days}} days)', {
                days,
              })}
        </h3>
        <div className="flex items-center gap-2 text-xs text-ink-400">
          <span className="tabular-nums">
            {t('aiUsage.pagination', '{{from}}–{{to}} of {{total}}', {
              from,
              to,
              total,
            })}
          </span>
          <button
            type="button"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded border border-ink-700 p-1 text-ink-300 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label={t('aiUsage.prevPage', 'Previous page')}
          >
            <ChevronLeft size={13} />
          </button>
          <button
            type="button"
            onClick={() => setPage((p) => p + 1)}
            disabled={page * RUNS_PAGE_SIZE >= total}
            className="rounded border border-ink-700 p-1 text-ink-300 hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label={t('aiUsage.nextPage', 'Next page')}
          >
            <ChevronRight size={13} />
          </button>
        </div>
      </div>
      {items.length === 0 ? (
        <div className="px-4 py-8 text-center text-sm text-ink-500">
          {t('aiUsage.runsEmpty', 'No calls in the last 30 days.')}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-ink-900/80 text-xs font-medium uppercase tracking-wide text-ink-500">
              <tr>
                <th className="px-4 py-2">{t('aiUsage.colTime', 'Time')}</th>
                <th className="px-4 py-2">{t('aiUsage.colAgent', 'Agent')}</th>
                <th className="px-4 py-2">{t('aiUsage.colModel', 'Model')}</th>
                <th className="px-4 py-2 text-right">
                  {t('aiUsage.colTokens', 'Tokens')}
                </th>
                <th className="px-4 py-2 text-right">
                  {t('aiUsage.colCost', 'Cost')}
                </th>
                <th className="px-4 py-2">{t('aiUsage.colStatus', 'Status')}</th>
                <th className="px-4 py-2 text-right">
                  {t('aiUsage.colDuration', 'Duration')}
                </th>
                <th className="px-4 py-2">
                  {t('aiUsage.colTrigger', 'Trigger')}
                </th>
                <th className="px-4 py-2">{t('aiUsage.colError', 'Error')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {items.map((r) => (
                <tr key={r.id} className="transition-colors hover:bg-ink-900">
                  <td className="whitespace-nowrap px-4 py-2 tabular-nums text-ink-300">
                    {r.started_at ? formatRunTime(r.started_at) : '—'}
                  </td>
                  <td className="px-4 py-2 text-ink-200">
                    {r.agent_name ?? r.agent_slug ?? '—'}
                  </td>
                  <td className="px-4 py-2">
                    <div className="text-ink-200">{r.model ?? '—'}</div>
                    {r.provider && (
                      <div className="text-xs text-ink-500">{r.provider}</div>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-ink-100">
                    {formatTokens(r.total_tokens)}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-ink-100">
                    {formatCost(r.cost_cents)}
                  </td>
                  <td className="px-4 py-2">
                    <StatusPill status={r.status} />
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-ink-300">
                    {formatDuration(r.duration_ms)}
                  </td>
                  <td className="px-4 py-2 text-xs text-ink-400">
                    {r.trigger ?? '—'}
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {r.error_code ? (
                      <span className="text-red-300">{r.error_code}</span>
                    ) : (
                      <span className="text-ink-600">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};

const StatusPill: React.FC<{ status: string }> = ({ status }) => {
  const styles: Record<string, string> = {
    completed: 'bg-emerald-500/10 text-emerald-300',
    running: 'bg-sky-500/10 text-sky-300',
    failed: 'bg-red-500/10 text-red-300',
    cancelled: 'bg-ink-700/60 text-ink-400',
    heartbeat_lost: 'bg-amber-500/10 text-warn',
  };
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-xs font-medium ${
        styles[status] ?? 'bg-ink-700/60 text-ink-400'
      }`}
    >
      {status.replace(/_/g, ' ')}
    </span>
  );
};

// ─── Formatters ────────────────────────────────────────────────────────────

/** YYYY-MM in UTC (matches backend _month_bounds's month parsing). */
function formatMonth(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  return `${y}-${m}`;
}

/** MM-DD HH:mm local time for the runs table. */
function formatRunTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatDuration(ms: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
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
