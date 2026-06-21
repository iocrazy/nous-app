// frontend/components/TopicInspiration/SourceHealthBadge.tsx
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity } from 'lucide-react';
import {
  getSourceHealth,
  type SourceHealth,
  type SourceHealthStatus,
} from '../../services/topicService';
import { summarizeSourceHealth } from './sourceHealthSummary';

// Status → dot color. Read-only surface, so colors are the whole signal.
const DOT: Record<SourceHealthStatus, string> = {
  ok: '#10b981', // emerald
  degraded: '#f59e0b', // amber
  dead: '#ef4444', // red
};

function timeAgo(iso?: string | null): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'never';
  const sec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

export const SourceHealthBadge: React.FC = () => {
  const { t } = useTranslation();
  const [sources, setSources] = useState<SourceHealth[]>([]);
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const s = await getSourceHealth();
        if (alive) setSources(s);
      } catch (err) {
        // Best-effort surface — never block the page on a health probe.
        console.error('source health load failed', err);
      } finally {
        if (alive) setLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  if (!loaded || sources.length === 0) return null;

  const summary = summarizeSourceHealth(sources);
  const unhealthy = summary.degraded + summary.dead;
  const label =
    unhealthy > 0
      ? t('topic.sourcesUnhealthy', { count: unhealthy, defaultValue: '{{count}} sources down' })
      : t('topic.sourcesHealthy', 'Sources OK');

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        title={t('topic.sourceHealth', 'Source health')}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-island-2 border border-line-strong text-content-2 hover:text-content transition-colors"
      >
        <span
          className="inline-block w-2 h-2 rounded-full"
          style={{ backgroundColor: DOT[summary.worst] }}
        />
        <Activity size={12} />
        <span>{label}</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-[300px] max-h-[70vh] overflow-auto rounded-xl border border-line-strong bg-island shadow-lg z-50 p-2">
          <div className="px-2 py-1.5 text-[11px] font-semibold text-content-3 uppercase tracking-wide">
            {t('topic.sourceHealth', 'Source health')} · {summary.ok}/{summary.total} OK
          </div>
          {sources.map((s) => (
            <div
              key={s.id}
              className="flex items-start gap-2 px-2 py-1.5 rounded-lg hover:bg-island-2"
            >
              <span
                className="mt-1 inline-block w-2 h-2 rounded-full shrink-0"
                style={{ backgroundColor: s.enabled ? DOT[s.health] : '#9ca3af' }}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[13px] font-medium text-content truncate">{s.name}</span>
                  <span className="text-[10px] text-content-4 shrink-0">{s.kind}</span>
                </div>
                <div className="text-[11px] text-content-3">
                  {!s.enabled
                    ? t('topic.sourceDisabled', 'Disabled')
                    : `${t('topic.lastOk', 'Last OK')}: ${timeAgo(s.last_ok_at)}`}
                </div>
                {s.enabled && s.health !== 'ok' && s.last_error && (
                  <div className="mt-0.5 text-[11px] text-[#dc2626] break-words">
                    {s.last_error}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
