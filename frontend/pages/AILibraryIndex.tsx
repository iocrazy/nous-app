// frontend/pages/AILibraryIndex.tsx
// Default landing for /ai-library — the workbench Dashboard (v2.1 IA).
//
// Was a bare redirect to /agents, which made the library open on a wall of
// system pipeline agents. Paperclip's entry point is a Dashboard with a
// live-run count; this is the mediahub equivalent, assembled from existing
// pieces only: LiveRunsStrip + the usage-summary endpoint. No new backend.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryUsageSummary } from '../types';
import { aiLibraryService } from '../services/aiLibraryService';
import { LiveRunsStrip } from '../components/AILibrary/LiveRunsStrip';
import { PageHeader } from '../components/AILibrary/PageHeader';

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-ink-800/60 bg-ink-900/20 px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-wider text-ink-600">
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold text-ink-200 tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-ink-600">{hint}</div>}
    </div>
  );
}

const fmt = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}k` : String(n);

export const AILibraryIndex: React.FC = () => {
  const { t } = useTranslation();
  const [today, setToday] = useState<AILibraryUsageSummary | null>(null);
  const [week, setWeek] = useState<AILibraryUsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [d1, d7] = await Promise.all([
          aiLibraryService.getUsageSummary(1),
          aiLibraryService.getUsageSummary(7),
        ]);
        if (!cancelled) {
          setToday(d1);
          setWeek(d7);
        }
      } catch (err) {
        console.error('[AILibraryIndex] usage summary failed:', err);
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const topModels = (week?.by_model ?? []).slice(0, 5);

  return (
    <div className="mx-auto max-w-4xl space-y-6 pb-12">
      <PageHeader title={t('sidebar.dashboard', 'Dashboard')} className="pb-0" />

      {/* Running now */}
      <LiveRunsStrip />

      {error ? (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          {t('aiLibrary.dashboard.loadError', 'Failed to load usage')}: {error}
        </div>
      ) : (
        <>
          {/* Today / 7-day stats */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard
              label={t('aiLibrary.dashboard.todayRuns', 'Runs today')}
              value={today ? fmt(today.overall.run_count) : '—'}
            />
            <StatCard
              label={t('aiLibrary.dashboard.todayTokens', 'Tokens today')}
              value={today ? fmt(today.overall.total_tokens) : '—'}
            />
            <StatCard
              label={t('aiLibrary.dashboard.weekRuns', 'Runs · 7d')}
              value={week ? fmt(week.overall.run_count) : '—'}
            />
            <StatCard
              label={t('aiLibrary.dashboard.weekTokens', 'Tokens · 7d')}
              value={week ? fmt(week.overall.total_tokens) : '—'}
            />
          </div>

          {/* Top models this week */}
          {topModels.length > 0 && (
            <div className="rounded-xl border border-ink-800/60 bg-ink-900/20">
              <div className="border-b border-ink-800/60 px-4 py-2.5 text-[11px] font-medium uppercase tracking-wider text-ink-600">
                {t('aiLibrary.dashboard.topModels', 'Top models · 7d')}
              </div>
              <div className="divide-y divide-ink-800/40">
                {topModels.map((m) => (
                  <div key={m.model} className="flex items-center gap-3 px-4 py-2 text-[13px]">
                    <span className="min-w-0 flex-1 truncate text-ink-300">{m.model}</span>
                    <span className="text-ink-500 tabular-nums">
                      {fmt(m.total_tokens)} tokens
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default AILibraryIndex;
