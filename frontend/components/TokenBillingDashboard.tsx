/**
 * Phase 3 — Token billing dashboard.
 *
 * Shows the current user's recent token usage from ai_usage_logs:
 *  - Overall stat tiles (tokens / points spent / run count)
 *  - By model breakdown (sorted by cost desc)
 *  - By day sparkline (just numbers — no chart library on this branch)
 *
 * Embedded in Settings → AI as a section. Defaults to 30-day window.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Coins, RefreshCw, BarChart3, Calendar } from 'lucide-react';
import { aiLibraryService } from '../services/aiLibraryService';
import { useToast } from './Toast';
import type { AILibraryUsageSummary } from '../types';

const WINDOW_OPTIONS = [7, 14, 30, 60, 90];

function _fmtNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function _fmtPoints(p: number): string {
  return p.toFixed(2);
}

export const TokenBillingDashboard: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [days, setDays] = useState(30);
  const [data, setData] = useState<AILibraryUsageSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await aiLibraryService.getUsageSummary(days);
      setData(resp);
    } catch (err) {
      console.error('[TokenBillingDashboard] load failed:', err);
      addToast(t('billing.loadFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [days, addToast, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const overall = data?.overall;
  const byModel = data?.by_model ?? [];
  const byDay = data?.by_day ?? [];
  // Max for sparkline normalization
  const maxDayCost = byDay.length > 0
    ? Math.max(...byDay.map((d) => d.cost_points))
    : 0;

  return (
    <div className="flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <div className="flex items-center gap-2">
          <Coins className="w-4 h-4 text-amber-400" />
          <h2 className="text-sm font-semibold text-zinc-200">{t('billing.title')}</h2>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="px-2 py-1 text-xs rounded bg-zinc-900 border border-zinc-800 text-zinc-300"
          >
            {WINDOW_OPTIONS.map((d) => (
              <option key={d} value={d}>
                {t('billing.lastNDays', { count: d })}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={reload}
            className="p-1.5 rounded text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
            title={t('billing.refresh')}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Stat tiles */}
      <div className="grid grid-cols-3 gap-3 px-4 py-4">
        <div className="bg-zinc-900/40 border border-zinc-800 rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wide text-zinc-500">
            {t('billing.statTokens')}
          </div>
          <div className="text-2xl font-bold mt-1 text-zinc-100">
            {overall ? _fmtNumber(overall.total_tokens) : '—'}
          </div>
        </div>
        <div className="bg-zinc-900/40 border border-zinc-800 rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wide text-zinc-500">
            {t('billing.statPoints')}
          </div>
          <div className="text-2xl font-bold mt-1 text-amber-300">
            {overall ? _fmtPoints(overall.cost_points) : '—'}
          </div>
        </div>
        <div className="bg-zinc-900/40 border border-zinc-800 rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wide text-zinc-500">
            {t('billing.statRuns')}
          </div>
          <div className="text-2xl font-bold mt-1 text-blue-300">
            {overall ? _fmtNumber(overall.run_count) : '—'}
          </div>
        </div>
      </div>

      {/* By-model table */}
      <div className="px-4 pb-4">
        <div className="flex items-center gap-2 mb-2 text-xs text-zinc-400">
          <BarChart3 className="w-3 h-3" />
          {t('billing.byModel')}
        </div>
        {byModel.length === 0 ? (
          <div className="text-xs text-zinc-500 py-3 text-center bg-zinc-900/30 rounded">
            {loading ? t('billing.loading') : t('billing.noUsage')}
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-zinc-500 text-left">
                <th className="font-medium pb-1">{t('billing.colModel')}</th>
                <th className="font-medium pb-1 text-right">{t('billing.colRuns')}</th>
                <th className="font-medium pb-1 text-right">{t('billing.colTokens')}</th>
                <th className="font-medium pb-1 text-right">{t('billing.colCost')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {byModel.map((m) => (
                <tr key={m.model} className="text-zinc-300">
                  <td className="py-1.5 font-mono text-[11px]">{m.model}</td>
                  <td className="py-1.5 text-right">{m.run_count}</td>
                  <td className="py-1.5 text-right">{_fmtNumber(m.total_tokens)}</td>
                  <td className="py-1.5 text-right text-amber-300">
                    {_fmtPoints(m.cost_points)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* By-day sparkline (text-based) */}
      {byDay.length > 0 && (
        <div className="px-4 pb-4">
          <div className="flex items-center gap-2 mb-2 text-xs text-zinc-400">
            <Calendar className="w-3 h-3" />
            {t('billing.byDay')}
          </div>
          <div className="flex items-end gap-1 h-16 bg-zinc-900/30 rounded p-2">
            {byDay.map((d) => {
              const heightPct = maxDayCost > 0
                ? Math.max(4, (d.cost_points / maxDayCost) * 100)
                : 4;
              return (
                <div
                  key={d.date}
                  className="flex-1 bg-amber-500/40 hover:bg-amber-500/70 rounded-sm relative group"
                  style={{ height: `${heightPct}%` }}
                  title={`${d.date}: ${_fmtPoints(d.cost_points)} pts / ${_fmtNumber(d.total_tokens)} tok`}
                />
              );
            })}
          </div>
          <div className="flex justify-between text-[10px] text-zinc-600 mt-1">
            <span>{byDay[0]?.date}</span>
            <span>{byDay[byDay.length - 1]?.date}</span>
          </div>
        </div>
      )}
    </div>
  );
};

export default TokenBillingDashboard;
