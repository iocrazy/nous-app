import React, { useCallback, useEffect, useState } from 'react';
import { Activity, CheckCircle2, AlertTriangle, XCircle, Loader2, RefreshCw } from 'lucide-react';
import { Loading } from './common/Loading';
import { useTranslation } from 'react-i18next';
import { getAIHealth, type CapabilityHealth } from '../services/aiService';

/**
 * Capability health board. Surfaces the otherwise-invisible
 * capability→agent→model→provider→key mapping with an actionable status
 * per AI feature, so a missing key or a text-model-on-a-vision-task is
 * visible at a glance instead of failing silently. Self-contained
 * section, mirrors the other AISettings sub-panels.
 */
export const AIHealthBoard: React.FC = () => {
  const { t } = useTranslation();
  const [rows, setRows] = useState<CapabilityHealth[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await getAIHealth());
    } catch (err) {
      setError(err instanceof Error ? err.message : t('aiHealth.loadError'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  // Defer the initial probe to idle time so this board's (potentially slow)
  // real health check never competes with the first paint of the AI settings
  // page — the rest of the tab renders immediately, this section fills in once
  // the browser is idle. The Loading skeleton covers the interim, and the
  // manual Refresh button still triggers an immediate reload.
  useEffect(() => {
    const w = window as typeof window & {
      requestIdleCallback?: (cb: () => void) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    if (typeof w.requestIdleCallback === 'function') {
      const id = w.requestIdleCallback(() => load());
      return () => w.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(() => load(), 0);
    return () => window.clearTimeout(id);
  }, [load]);

  const warnings = rows?.filter((r) => r.status !== 'ok').length ?? 0;

  return (
    <section className="mb-6 bg-ink-900/40 border border-ink-800 rounded-lg overflow-hidden">
      <div className="px-6 py-4 flex items-center gap-3 border-b border-ink-800">
        <div className="p-2 rounded-lg bg-sky-500/10 text-sky-400">
          <Activity size={18} />
        </div>
        <div className="flex-1">
          <h3 className="font-semibold text-ink-200">{t('aiHealth.title')}</h3>
          <p className="text-xs text-ink-500 mt-0.5">{t('aiHealth.subtitle')}</p>
        </div>
        {warnings > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full border bg-amber-500/15 text-warn border-amber-500/30">
            {t('aiHealth.warningsBadge', { count: warnings })}
          </span>
        )}
        <button
          onClick={load}
          disabled={loading}
          title={t('aiHealth.refresh')}
          className="text-ink-500 hover:text-ink-200 transition-colors disabled:opacity-50"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
        </button>
      </div>

      <div className="p-4">
        {error ? (
          <div className="flex items-center gap-2 text-xs text-red-400 px-2 py-1">
            <AlertTriangle size={14} />
            {error}
          </div>
        ) : !rows ? (
          <div className="flex items-center gap-2 text-sm text-ink-500 px-2 py-1">
            <Loading label={t('common.loading')} />
          </div>
        ) : (
          <ul className="space-y-1">
            {rows.map((r) => (
              <HealthRow key={r.capability} row={r} t={t} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
};

/**
 * Which resolution branch serves this capability. "env" (the shared
 * no-provider fall-through) and error rows ('') render nothing — an origin
 * badge on an unconfigured row would just be noise.
 */
const OriginBadge: React.FC<{
  origin?: string;
  t: (k: string, o?: object) => string;
}> = ({ origin, t }) => {
  if (origin === 'platform') {
    return (
      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-sky-500/15 text-sky-300 border border-sky-500/30">
        {t('aiHealth.originPlatform')}
      </span>
    );
  }
  if (origin === 'governance') {
    return (
      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-500/15 text-violet-300 border border-violet-500/30">
        {t('aiHealth.originManaged')}
      </span>
    );
  }
  if (origin === 'byok') {
    return (
      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
        {t('aiHealth.originByok')}
      </span>
    );
  }
  return null;
};

const HealthRow: React.FC<{ row: CapabilityHealth; t: (k: string, o?: object) => string }> = ({
  row,
  t,
}) => {
  const ok = row.status === 'ok';
  // runtime_failing / probe_failing / key_test_failed all mean the config
  // resolves but the provider is UNREACHABLE right now (rejected call,
  // failed platform probe, failed key test) — that's broken now, so they
  // read as errors (red), not configuration warnings (amber).
  const isError =
    row.status === 'error' ||
    row.status === 'runtime_failing' ||
    row.status === 'probe_failing' ||
    row.status === 'key_test_failed';
  const runs = row.recent_runs ?? 0;
  const failures = row.recent_failures ?? 0;
  return (
    <li className="flex items-start gap-3 px-2 py-2 rounded-lg hover:bg-ink-800/40 transition-colors">
      <span className="mt-0.5 shrink-0">
        {ok ? (
          <CheckCircle2 size={15} className="text-emerald-400" />
        ) : isError ? (
          <XCircle size={15} className="text-red-400" />
        ) : (
          <AlertTriangle size={15} className="text-amber-400" />
        )}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-ink-200">{row.label}</span>
          {row.model ? (
            <span className="text-xs font-mono text-ink-500">
              {row.agent_slug ? `${row.agent_slug} → ${row.model}` : row.model}
            </span>
          ) : (
            <span className="text-xs text-ink-600">{t('aiHealth.unset')}</span>
          )}
          <OriginBadge origin={row.origin} t={t} />
          {!row.assigned && row.agent_slug && row.model && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-ink-800 text-ink-500 border border-ink-700">
              {t('aiHealth.default')}
            </span>
          )}
          {failures > 0 ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/15 text-red-300 border border-red-500/30">
              {t('aiHealth.recentFailures', { failures, runs })}
            </span>
          ) : ok && runs > 0 ? (
            <span className="text-[10px] text-ink-600">{t('aiHealth.recentOk', { runs })}</span>
          ) : null}
        </div>
        {!ok && row.hint && (
          <p className={`text-xs mt-0.5 ${isError ? 'text-red-400/80' : 'text-amber-400/80'}`}>
            {row.hint}
          </p>
        )}
      </div>
    </li>
  );
};

export default AIHealthBoard;
