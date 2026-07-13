import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink } from 'lucide-react';
import { useToast } from '../Toast';
import { useTaskManager } from '../../contexts/TaskManagerContext';
import {
  getShareSchema, listAccounts, listPublishTasks, retryPublishTask,
} from '../../services/distributionService';
import { PublishTask, PublishTaskAccount, SocialAccount } from '../../types';
import './distribution-v4.css';

type Filter = 'all' | 'needs_action' | 'publishing' | 'done';

const STATUS_META: Record<PublishTask['status'], { cls: string; pulse?: boolean }> = {
  success: { cls: 'chip-green' },
  partial: { cls: 'chip-amber' },
  pending_share: { cls: 'chip-amber' },
  publishing: { cls: 'chip-indigo', pulse: true },
  failed: { cls: 'chip-red' },
  pending: { cls: 'chip-mute' },
};

const ACCOUNT_STATUS_META: Record<PublishTaskAccount['status'], { cls: string; pulse?: boolean }> = {
  success: { cls: 'chip-green' },
  pending_share: { cls: 'chip-amber' },
  publishing: { cls: 'chip-indigo', pulse: true },
  failed: { cls: 'chip-red' },
  pending: { cls: 'chip-mute' },
  cancelled: { cls: 'chip-mute' },
};

const PLATFORM_LABEL: Record<string, string> = {
  douyin: 'Douyin', kuaishou: 'Kuaishou', xiaohongshu: 'Xiaohongshu',
};

const REC_COVERS = [
  'radial-gradient(60px 40px at 70% 20%, rgba(255,196,120,.55), transparent 70%), linear-gradient(170deg,#46346e,#23375f 55%,#132c47)',
  'radial-gradient(50px 35px at 30% 25%, rgba(120,220,255,.4), transparent 70%), linear-gradient(170deg,#6e3446,#4c2b5e 60%,#1e1e3a)',
  'linear-gradient(160deg,#174a3b,#2f5563)',
  'linear-gradient(160deg,#31174a,#63472f)',
];
const AVA_GRADIENTS = [
  'linear-gradient(135deg,#0ea5e9,#6366f1)',
  'linear-gradient(135deg,#8b5cf6,#ec4899)',
  'linear-gradient(135deg,#f97316,#ef4444)',
  'linear-gradient(135deg,#10b981,#0ea5e9)',
];
const hash = (id: string): number => {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h;
};
const coverFor = (id: string): string => REC_COVERS[hash(id) % REC_COVERS.length];
const gradientFor = (id: string): string => AVA_GRADIENTS[hash(id) % AVA_GRADIENTS.length];

const matchesFilter = (t: PublishTask, f: Filter): boolean => {
  if (f === 'all') return true;
  if (f === 'needs_action') return t.status === 'pending_share' || t.status === 'failed' || t.status === 'partial';
  if (f === 'publishing') return t.status === 'publishing' || t.status === 'pending';
  return t.status === 'success';
};

const dayKey = (iso: string): string => iso.slice(0, 10);

const formatTime = (iso: string): string => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

export const RecordsPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [tasks, setTasks] = useState<PublishTask[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const reload = useCallback(async () => {
    try {
      const [t2, a] = await Promise.all([listPublishTasks(), listAccounts()]);
      setTasks(t2);
      setAccounts(a);
    } catch (err) {
      console.error('distribution: list publish tasks failed', err);
      addToast(t('distribution.records.loadFailed', 'Failed to load records'), 'error');
    }
  }, [addToast, t]);

  useEffect(() => { void reload(); }, [reload]);

  // ── DBOS-driven live sync (route C) ──
  // task_tracking is the execution engine's single UI source of truth, and
  // TaskManagerContext already subscribes to it over Supabase Realtime. When
  // any publish workflow row changes phase (queued → processing → completed /
  // failed / cancelled), the signature below changes and we re-pull the
  // records — no page-level polling of the publish API while workflows run.
  const { tasks: trackedTasks } = useTaskManager();
  const publishSignature = useMemo(
    () => trackedTasks
      .filter((tk) => tk.task_type === 'publish')
      .map((tk) => `${tk.id}:${tk.status}`)
      .sort()
      .join('|'),
    [trackedTasks],
  );
  useEffect(() => {
    if (publishSignature) void reload();
  }, [publishSignature, reload]);

  // H5 hand-off rows flip pending_share → success via the Douyin WEBHOOK,
  // which writes business state only (publish_task_accounts) — task_tracking
  // never changes, so Realtime can't observe it. Poll gently, and only while
  // such rows exist.
  const hasPendingShare = useMemo(
    () => tasks.some((tk) => tk.status === 'pending_share'
      || tk.accounts.some((a) => a.status === 'pending_share')),
    [tasks],
  );
  useEffect(() => {
    if (!hasPendingShare) return undefined;
    const timer = window.setInterval(() => { void reload(); }, 15_000);
    return () => window.clearInterval(timer);
  }, [hasPendingShare, reload]);

  const platformFor = useCallback(
    (accountId: string): string | null => {
      const acc = accounts.find((a) => a.id === accountId);
      return acc ? (PLATFORM_LABEL[acc.platform] ?? acc.platform) : null;
    },
    [accounts],
  );

  const taskStatusLabel = useCallback(
    (status: PublishTask['status']): string => {
      switch (status) {
        case 'success': return t('distribution.records.status_success', 'Published');
        case 'partial': return t('distribution.records.status_partial', 'Partially published');
        case 'pending_share': return t('distribution.records.statusPendingShare', 'Waiting in Douyin');
        case 'publishing': return t('distribution.records.status_publishing', 'Publishing');
        case 'failed': return t('distribution.records.status_failed', 'Failed');
        case 'pending': return t('distribution.records.statusQueued', 'Queued');
        default: return status;
      }
    },
    [t],
  );

  const accountStatusLabel = useCallback(
    (status: PublishTaskAccount['status']): string => {
      switch (status) {
        case 'success': return t('distribution.records.status_success', 'Published');
        case 'pending_share': return t('distribution.records.statusPendingShare', 'Waiting in Douyin');
        case 'publishing': return t('distribution.records.status_publishing', 'Publishing');
        case 'failed': return t('distribution.records.status_failed', 'Failed');
        case 'pending': return t('distribution.records.statusQueued', 'Queued');
        case 'cancelled': return t('distribution.records.status_cancelled', 'Cancelled');
        default: return status;
      }
    },
    [t],
  );

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

  const needsActionCount = useMemo(() => tasks.filter((x) => matchesFilter(x, 'needs_action')).length, [tasks]);
  const publishingCount = useMemo(() => tasks.filter((x) => matchesFilter(x, 'publishing')).length, [tasks]);

  const FILTERS: { key: Filter; label: string; count?: number }[] = [
    { key: 'all', label: t('distribution.records.all', 'All') },
    { key: 'needs_action', label: t('distribution.records.needsAction', 'Needs action'), count: needsActionCount },
    { key: 'publishing', label: t('distribution.records.publishing', 'Publishing'), count: publishingCount },
    { key: 'done', label: t('distribution.records.done', 'Completed') },
  ];

  return (
    <div className="dist-v4">
      <div className="page-head">
        <div>
          <h2>
            {t('distribution.records.title', 'Publish Records')}
            <span className="count">{tasks.length}</span>
          </h2>
          <p>{t('distribution.records.subtitle', 'Every publish task across accounts, live from Task Center.')}</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" disabled title={t('distribution.comingInD3', 'Coming in D3')}>{t('distribution.records.export', 'Export')}</button>
      </div>

      <div className="filters">
        {FILTERS.map((f) => {
          const isActive = filter === f.key;
          const cls = isActive ? 'chip-indigo' : (f.key === 'needs_action' && (f.count ?? 0) > 0 ? 'chip-amber' : 'chip-mute');
          return (
            <button
              key={f.key}
              type="button"
              className={`chip ${cls}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label}{f.count ? ` · ${f.count}` : ''}
            </button>
          );
        })}
      </div>

      {groups.length === 0 && (
        <p className="text-sm" style={{ color: 'var(--content-4)', marginTop: 18 }}>
          {t('distribution.records.empty', 'No records yet')}
        </p>
      )}

      {groups.map(([day, dayTasks]) => (
        <div key={day}>
          <div className="day-label">{day}</div>
          {dayTasks.map((task) => {
            const open = Boolean(expanded[task.id]);
            const chipMeta = STATUS_META[task.status];
            const chipLabel = taskStatusLabel(task.status);
            const singleAccount = task.accounts.length === 1 ? task.accounts[0] : null;
            const accountSummary = singleAccount
              ? `${singleAccount.username}${platformFor(singleAccount.account_id) ? ` (${platformFor(singleAccount.account_id)})` : ''}`
              : t('distribution.records.accountsCount', '{{n}} accounts', { n: task.accounts.length });
            const contentLabel = task.content_type === 'video'
              ? t('distribution.records.contentVideo', 'Video')
              : task.content_type === 'images'
                ? t('distribution.records.contentImages', 'Images')
                : t('distribution.records.contentArticle', 'Article');

            return (
              <div key={task.id} className={`rec ${task.status === 'pending_share' ? 'attn' : ''}`}>
                <div
                  className="rec-head"
                  role="button"
                  tabIndex={0}
                  onClick={() => setExpanded((s) => ({ ...s, [task.id]: !s[task.id] }))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setExpanded((s) => ({ ...s, [task.id]: !s[task.id] }));
                    }
                  }}
                >
                  <span className="cover" style={{ background: coverFor(task.id) }} />
                  <div className="info">
                    <b>{task.title}</b>
                    <span>
                      {contentLabel}<span className="sep">·</span>{accountSummary}<span className="sep">·</span>{formatTime(task.created_at)}
                    </span>
                  </div>
                  <span className={`chip ${chipMeta.cls} ${chipMeta.pulse ? 'pulse' : ''}`}>
                    <span className="d" />{chipLabel}
                  </span>
                  {task.status === 'pending_share' && (
                    <button
                      type="button"
                      className="btn btn-tint-amber btn-sm"
                      onClick={(e) => { e.stopPropagation(); void onFinishH5(task.id); }}
                    >
                      <ExternalLink size={13} /> {t('distribution.records.openDouyin', 'Open Douyin to finish')}
                    </button>
                  )}
                </div>

                {open && task.accounts.length > 0 && (
                  <div className="rec-sub">
                    {task.accounts.map((a) => {
                      const aChipMeta = ACCOUNT_STATUS_META[a.status];
                      const aChipLabel = accountStatusLabel(a.status);
                      const platform = platformFor(a.account_id);
                      return (
                        <div key={a.id} className="sub-row">
                          <span className="ava" style={{ background: gradientFor(a.account_id) }}>
                            {a.username.slice(0, 2).toUpperCase()}
                          </span>
                          <span className="who">
                            {a.username}{platform ? ` · ${platform}` : ''}
                          </span>
                          <span className={`chip ${aChipMeta.cls} ${aChipMeta.pulse ? 'pulse' : ''}`}>
                            <span className="d" />{aChipLabel}
                          </span>
                          {a.status === 'success' && a.published_url && (
                            <a href={a.published_url} target="_blank" rel="noreferrer">
                              {t('distribution.records.view', 'View post')}
                            </a>
                          )}
                          {a.status === 'failed' && (
                            <>
                              <span className="err">{a.error_message}</span>
                              <button type="button" className="btn btn-ghost btn-sm" onClick={() => void onRetry(task.id)}>
                                {t('distribution.records.retry', 'Retry')}
                              </button>
                            </>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
};

export default RecordsPage;
