// frontend/components/TopicInspiration/SourceHealthBadge.tsx
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, Eye, EyeOff, Trash2, Plus, Loader2 } from 'lucide-react';
import {
  getSourceHealth,
  addSource,
  deleteSource,
  setSourceHidden,
  type SourceHealth,
  type SourceHealthStatus,
  type SourceKind,
} from '../../services/topicService';
import { summarizeSourceHealth } from './sourceHealthSummary';
import { UiSelect } from '../ui';

// Status → dot color. Read-only surface, so colors are the whole signal.
const DOT: Record<SourceHealthStatus, string> = {
  ok: '#10b981', // emerald
  degraded: '#f59e0b', // amber
  dead: '#ef4444', // red
};

const KINDS: SourceKind[] = ['newsnow', 'rss', 'http_api', 'custom'];

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

// Map the single config input to the shape each adapter expects.
function buildConfig(kind: SourceKind, value: string): Record<string, unknown> {
  const v = value.trim();
  if (!v) return {};
  if (kind === 'newsnow') return { id: v };
  if (kind === 'rss' || kind === 'http_api') return { url: v };
  return {};
}

export const SourceHealthBadge: React.FC = () => {
  const { t } = useTranslation();
  const [sources, setSources] = useState<SourceHealth[]>([]);
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // Add-source form state.
  const [adding, setAdding] = useState(false);
  const [fKind, setFKind] = useState<SourceKind>('rss');
  const [fName, setFName] = useState('');
  const [fConfig, setFConfig] = useState('');
  const [fCategory, setFCategory] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const reload = async () => {
    try {
      const s = await getSourceHealth();
      setSources(s);
    } catch (err) {
      console.error('source health load failed', err);
    } finally {
      setLoaded(true);
    }
  };

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

  // The toolbar badge needs a stable anchor even before sources load, so the
  // popover (with its Add form) is reachable on an empty/fresh account.
  if (!loaded) return null;

  const summary =
    sources.length > 0
      ? summarizeSourceHealth(sources)
      : { ok: 0, degraded: 0, dead: 0, total: 0, worst: 'ok' as SourceHealthStatus };
  const unhealthy = summary.degraded + summary.dead;
  const label =
    unhealthy > 0
      ? t('topic.sourcesUnhealthy', { count: unhealthy, defaultValue: '{{count}} sources down' })
      : t('topic.sourcesHealthy', 'Sources OK');

  const toggleHidden = async (s: SourceHealth) => {
    setBusyId(s.id);
    try {
      await setSourceHidden(s.id, !s.is_hidden);
      setSources((prev) =>
        prev.map((x) => (x.id === s.id ? { ...x, is_hidden: !s.is_hidden } : x)),
      );
    } catch (err) {
      console.error('toggle hidden failed', err);
    } finally {
      setBusyId(null);
    }
  };

  const removeSource = async (s: SourceHealth) => {
    if (!window.confirm(t('topic.confirmDeleteSource', 'Delete this source? It stops collecting.'))) {
      return;
    }
    setBusyId(s.id);
    try {
      await deleteSource(s.id);
      setSources((prev) => prev.filter((x) => x.id !== s.id));
    } catch (err) {
      console.error('delete source failed', err);
    } finally {
      setBusyId(null);
    }
  };

  const submitNew = async () => {
    const name = fName.trim();
    if (!name) {
      setFormError(t('topic.sourceNameRequired', 'Name is required'));
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await addSource({
        kind: fKind,
        name,
        category: fCategory.trim() || null,
        config: buildConfig(fKind, fConfig),
      });
      setFName('');
      setFConfig('');
      setFCategory('');
      setAdding(false);
      await reload();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to add source');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        title={t('topic.manageSources', 'Manage sources')}
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
        <div className="absolute right-0 mt-2 w-[330px] max-h-[70vh] overflow-auto rounded-xl border border-line-strong bg-island shadow-lg z-50 p-2">
          <div className="flex items-center justify-between px-2 py-1.5">
            <span className="text-[11px] font-semibold text-content-3 uppercase tracking-wide">
              {t('topic.sources', 'Sources')} · {summary.ok}/{summary.total} {t('topic.ok', 'OK')}
            </span>
            <button
              onClick={() => setAdding((v) => !v)}
              className="flex items-center gap-1 text-[11px] font-medium text-content-2 hover:text-content"
            >
              <Plus size={12} /> {t('topic.addSource', 'Add')}
            </button>
          </div>

          {adding && (
            <div className="mx-1 mb-2 p-2 rounded-lg border border-line-strong bg-island-2 space-y-1.5">
              <div className="flex gap-1.5">
                <UiSelect
                  value={fKind}
                  onChange={(e) => setFKind(e.target.value as SourceKind)}
                  className="w-28 shrink-0 h-8 text-xs"
                >
                  {KINDS.map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
                </UiSelect>
                <input
                  value={fName}
                  onChange={(e) => setFName(e.target.value)}
                  placeholder={t('topic.sourceName', 'Name')}
                  className="flex-1 min-w-0 text-[12px] px-1.5 py-1 rounded-md bg-island border border-line-strong text-content"
                />
              </div>
              {fKind !== 'custom' && (
                <input
                  value={fConfig}
                  onChange={(e) => setFConfig(e.target.value)}
                  placeholder={
                    fKind === 'newsnow'
                      ? t('topic.sourcePlatformId', 'Platform ID')
                      : t('topic.sourceUrl', 'Feed URL')
                  }
                  className="w-full text-[12px] px-1.5 py-1 rounded-md bg-island border border-line-strong text-content"
                />
              )}
              <input
                value={fCategory}
                onChange={(e) => setFCategory(e.target.value)}
                placeholder={t('topic.sourceCategory', 'Category (optional)')}
                className="w-full text-[12px] px-1.5 py-1 rounded-md bg-island border border-line-strong text-content"
              />
              {formError && <div className="text-[11px] text-[#dc2626]">{formError}</div>}
              <div className="flex justify-end gap-1.5 pt-0.5">
                <button
                  onClick={() => {
                    setAdding(false);
                    setFormError(null);
                  }}
                  className="text-[11px] px-2 py-1 rounded-md text-content-3 hover:text-content"
                >
                  {t('common.cancel', 'Cancel')}
                </button>
                <button
                  onClick={submitNew}
                  disabled={submitting}
                  className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-md bg-accent text-white font-medium disabled:opacity-60"
                >
                  {submitting && <Loader2 size={11} className="animate-spin" />}
                  {t('topic.addSource', 'Add')}
                </button>
              </div>
            </div>
          )}

          {sources.length === 0 && !adding && (
            <div className="px-2 py-3 text-[12px] text-content-3 text-center">
              {t('topic.noSources', 'No sources yet. Add one to start collecting.')}
            </div>
          )}

          {sources.map((s) => (
            <div
              key={s.id}
              className={`flex items-start gap-2 px-2 py-1.5 rounded-lg hover:bg-island-2 ${
                s.is_hidden ? 'opacity-55' : ''
              }`}
            >
              <span
                className="mt-1 inline-block w-2 h-2 rounded-full shrink-0"
                style={{ backgroundColor: s.enabled ? DOT[s.health] : '#9ca3af' }}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-[13px] font-medium text-content truncate">{s.name}</span>
                  {s.is_owner && (
                    <span className="text-[9px] px-1 py-px rounded bg-accent/15 text-accent shrink-0">
                      {t('topic.ownSource', 'Yours')}
                    </span>
                  )}
                  <span className="text-[10px] text-content-4 shrink-0 ml-auto">{s.kind}</span>
                </div>
                <div className="text-[11px] text-content-3">
                  {s.is_hidden
                    ? t('topic.hiddenFromFeed', 'Hidden from your feed')
                    : !s.enabled
                      ? t('topic.sourceDisabled', 'Disabled')
                      : `${t('topic.lastOk', 'Last OK')}: ${timeAgo(s.last_ok_at)}`}
                </div>
                {s.enabled && !s.is_hidden && s.health !== 'ok' && s.last_error && (
                  <div className="mt-0.5 text-[11px] text-[#dc2626] break-words">{s.last_error}</div>
                )}
              </div>
              <div className="flex items-center gap-0.5 shrink-0">
                <button
                  onClick={() => toggleHidden(s)}
                  disabled={busyId === s.id}
                  title={
                    s.is_hidden
                      ? t('topic.unhideSource', 'Show in my feed')
                      : t('topic.hideSource', 'Hide from my feed')
                  }
                  className="p-1 rounded-md text-content-3 hover:text-content hover:bg-island disabled:opacity-50"
                >
                  {s.is_hidden ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
                {s.is_owner && (
                  <button
                    onClick={() => removeSource(s)}
                    disabled={busyId === s.id}
                    title={t('topic.deleteSource', 'Delete source')}
                    className="p-1 rounded-md text-content-3 hover:text-[#dc2626] hover:bg-island disabled:opacity-50"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
