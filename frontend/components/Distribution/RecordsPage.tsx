import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ExternalLink, RefreshCw } from 'lucide-react';
import { PageHeader } from '../AILibrary/PageHeader';
import { useToast } from '../Toast';
import {
  getShareSchema, listPublishTasks, retryPublishTask,
} from '../../services/distributionService';
import { PublishTask } from '../../types';

type Filter = 'all' | 'needs_action' | 'publishing' | 'done';

const STATUS_TINT: Record<string, string> = {
  success: 'text-emerald-300',
  partial: 'text-amber-300',
  pending_share: 'text-amber-300',
  publishing: 'text-sky-300',
  failed: 'text-red-400',
  pending: 'text-ink-400',
};

const matchesFilter = (t: PublishTask, f: Filter): boolean => {
  if (f === 'all') return true;
  if (f === 'needs_action') return t.status === 'pending_share' || t.status === 'failed' || t.status === 'partial';
  if (f === 'publishing') return t.status === 'publishing' || t.status === 'pending';
  return t.status === 'success';
};

const dayKey = (iso: string): string => iso.slice(0, 10);

export const RecordsPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [tasks, setTasks] = useState<PublishTask[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const reload = useCallback(async () => {
    try {
      setTasks(await listPublishTasks());
    } catch (err) {
      console.error('distribution: list publish tasks failed', err);
      addToast(t('distribution.records.loadFailed', 'Failed to load records'), 'error');
    }
  }, [addToast, t]);

  useEffect(() => { void reload(); }, [reload]);

  const onRetry = async (id: string) => {
    try {
      await retryPublishTask(id);
      addToast(t('distribution.records.retried', 'Retrying'), 'success');
      void reload();
    } catch (err) {
      console.error('distribution: retry failed', err);
      addToast(t('distribution.records.retryFailed', 'Retry failed'), 'error');
    }
  };

  const onFinishH5 = async (id: string) => {
    try {
      const { schema_url } = await getShareSchema(id);
      window.location.href = schema_url;
    } catch (err) {
      console.error('distribution: share schema failed', err);
      addToast(t('distribution.records.shareFailed', 'Could not open Douyin'), 'error');
    }
  };

  const filtered = useMemo(() => tasks.filter((x) => matchesFilter(x, filter)), [tasks, filter]);
  const groups = useMemo(() => {
    const m = new Map<string, PublishTask[]>();
    for (const task of filtered) {
      const k = dayKey(task.created_at);
      m.set(k, [...(m.get(k) ?? []), task]);
    }
    return Array.from(m.entries());
  }, [filtered]);

  const FILTERS: { key: Filter; label: string }[] = [
    { key: 'all', label: t('distribution.records.all', 'All') },
    { key: 'needs_action', label: t('distribution.records.needsAction', 'Needs action') },
    { key: 'publishing', label: t('distribution.records.publishing', 'Publishing') },
    { key: 'done', label: t('distribution.records.done', 'Published') },
  ];

  return (
    <div className="pt-6">
      <PageHeader
        title={t('distribution.records.title', 'Records')}
        count={tasks.length}
        subtitle={t('distribution.records.subtitle', 'Publish history across your accounts')}
      />
      <div className="mb-4 flex gap-2">
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => setFilter(f.key)}
            className={`rounded-full border px-3 py-1 text-[12px] ${
              filter === f.key ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300'
                               : 'border-ink-800 text-ink-400 hover:border-ink-700'}`}>
            {f.label}
          </button>
        ))}
      </div>

      {groups.length === 0 && (
        <p className="text-sm text-ink-500">{t('distribution.records.empty', 'No records yet')}</p>
      )}

      <div className="flex flex-col gap-5">
        {groups.map(([day, dayTasks]) => (
          <div key={day}>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-ink-600">{day}</h3>
            <div className="flex flex-col gap-2">
              {dayTasks.map((task) => {
                const open = expanded[task.id];
                return (
                  <div key={task.id}
                    className={`rounded-xl border p-3 ${
                      task.status === 'pending_share'
                        ? 'border-amber-500/40 bg-amber-500/5' : 'border-ink-800 bg-ink-900/50'}`}>
                    <button onClick={() => setExpanded((s) => ({ ...s, [task.id]: !s[task.id] }))}
                      className="flex w-full items-center justify-between text-left">
                      <span className="truncate text-[13.5px] font-medium text-ink-100">{task.title}</span>
                      <span className="flex items-center gap-2">
                        <span className={`text-[11px] ${STATUS_TINT[task.status] ?? 'text-ink-400'}`}>
                          {t(`distribution.records.status_${task.status}`, task.status)}
                        </span>
                        <ChevronDown size={15} className={`text-ink-600 transition-transform ${open ? 'rotate-180' : ''}`} />
                      </span>
                    </button>

                    {task.status === 'pending_share' && (
                      <button onClick={() => onFinishH5(task.id)}
                        className="btn-tint-amber mt-2 flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px]">
                        <ExternalLink size={13} /> {t('distribution.records.openDouyin', 'Open Douyin to finish')}
                      </button>
                    )}
                    {(task.status === 'failed' || task.status === 'partial') && (
                      <button onClick={() => onRetry(task.id)}
                        className="btn-tint-indigo mt-2 flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px]">
                        <RefreshCw size={13} /> {t('distribution.records.retry', 'Retry')}
                      </button>
                    )}

                    {open && (
                      <div className="mt-3 flex flex-col gap-1.5 border-t border-ink-800 pt-2.5">
                        {task.accounts.map((a) => (
                          <div key={a.id} className="flex items-center justify-between text-[12px]">
                            <span className="text-ink-300">{a.username}</span>
                            <span className="flex items-center gap-2">
                              {a.status === 'success' && a.published_url ? (
                                <a href={a.published_url} target="_blank" rel="noreferrer"
                                  className="flex items-center gap-1 text-emerald-300 hover:underline">
                                  <ExternalLink size={12} /> {t('distribution.records.view', 'View')}
                                </a>
                              ) : a.status === 'failed' ? (
                                <span className="text-red-400">{a.error_message}</span>
                              ) : a.status === 'publishing' ? (
                                <span className="animate-pulse text-sky-300">
                                  {t('distribution.records.status_publishing', 'publishing')}
                                </span>
                              ) : (
                                <span className={STATUS_TINT[a.status] ?? 'text-ink-400'}>
                                  {t(`distribution.records.status_${a.status}`, a.status)}
                                </span>
                              )}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default RecordsPage;
