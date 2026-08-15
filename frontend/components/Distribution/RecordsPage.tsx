import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink } from 'lucide-react';
import { useToast } from '../Toast';
import PublishSmsPrompt from './PublishSmsPrompt';
import { useTaskManager } from '../../contexts/TaskManagerContext';
import {
  getReadbackTiming, getShareSchema, isScheduleUnreachable, listAccounts, listPublishTasks,
  retryPublishTask, type ReadbackTiming,
} from '../../services/distributionService';
import { PublishTask, PublishTaskAccount, SocialAccount } from '../../types';
import { humanizeTaskError } from '../../utils/humanizeTaskError';
import { PageHeader } from '../layout/PageHeader';
import { AccountAvatar } from './platform';
import './distribution-v4.css';

type Filter = 'all' | 'needs_action' | 'publishing' | 'failed' | 'done';

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

// Where to send someone whose post has no direct link. The session channel
// cannot produce one: Douyin's post-publish redirect lands on the content
// manager with no item id in the URL, the post cards carry neither an href nor
// an id attribute, and no listing XHR returns one (all three verified against
// the live console, 2026-08-08). Guessing "the newest card" would mislabel the
// row for any account with a scheduled or concurrent post.
//
// Keyed by the raw platform key, NOT the display label — `platformFor` returns
// the label ('Douyin'), which would never match here.
const PLATFORM_MANAGER_URL: Record<string, string> = {
  douyin: 'https://creator.douyin.com/creator-micro/content/manage',
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
  if (f === 'failed') return t.status === 'failed' || t.status === 'partial';
  return t.status === 'success';
};

const dayKey = (iso: string): string => iso.slice(0, 10);

/** ISO day → local calendar day string for Today/Yesterday comparison. */
const localDayKey = (d: Date): string => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

const formatTime = (iso: string): string => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

/**
 * `[reason] prose` → the i18n key that explains it.
 *
 * The browser service prefixes every publish outcome note with a typed reason
 * (`publish_distribution.publish_notes`), and the reason is the contract — the
 * English prose that follows it is written for logs. Echoing that prose is how
 * a user ends up reading "published with 'X' — the closest match the platform's
 * search returned", which can never be translated.
 *
 * `humanizeTaskError` stays the fallback: it is the right tool for the rows
 * that carry no reason code at all (downloads, older publishes, raw exception
 * chains). This table is the structured path, the same split
 * `utils/errorCatalog.ts` describes.
 */
export const PUBLISH_NOTE_KEYS: ReadonlyArray<{
  test: RegExp;
  key: string;
  fallback: string;
}> = [
  {
    // The music the platform actually attached is not the one that was typed —
    // the search is fuzzy and rarely answers with a character-identical title.
    // The post DID go out with music, so this is a caveat, not a failure.
    test: /\[music_approximate\]/i,
    key: 'distribution.records.noteMusicApproximate',
    fallback: 'Published, but with the closest matching track rather than the one you named.',
  },
  {
    test: /\[music_not_found\]/i,
    key: 'distribution.records.noteMusicNotFound',
    fallback: "The platform's music search found nothing for that name — nothing was published. Try another spelling.",
  },
  {
    // Several results were indistinguishable from the track that was picked
    // (the platform lists same-titled uploads by different creators), so the
    // publish refused rather than guess. This sits ABOVE the catch-all below
    // because the user's move is different and specific: the generic sentence
    // ("the control could not be driven") would send him looking for a broken
    // picker that is working exactly as intended.
    test: /\[music_ambiguous\]/i,
    key: 'distribution.records.noteMusicAmbiguous',
    fallback: 'Several tracks on the platform look identical to the one you picked, so nothing was published rather than risk the wrong song. Pick a different version.',
  },
  {
    // Every other music reason is a control that could not be driven:
    // music_entry_missing / music_dialog_stuck / music_click_failed /
    // music_not_confirmed. One sentence, because the user's move is the same
    // for all four and the raw reason is still on the `title` attribute.
    test: /\[music_[a-z_]+\]/i,
    key: 'distribution.records.noteMusicFailed',
    fallback: 'The music could not be selected on the platform, so nothing was published.',
  },
  {
    test: /\[collection_(not_found|control_missing|error)\]/i,
    key: 'distribution.records.noteCollectionSkipped',
    fallback: 'Published, but the collection was not applied.',
  },
];

/**
 * Where a session-channel post stands with the PLATFORM, which is a different
 * question from whether our upload finished.
 *
 * `status: 'success'` answers "did we finish typing it in and pressing
 * publish". Whether the platform then put it live is answered minutes later by
 * `publish_readback`, and until that verdict arrives there is genuinely
 * nothing to claim. Both used to render as a flat "Published", which is how a
 * work item correctly held for confirmation became indistinguishable from a
 * stuck one.
 */
type VerifyDisplay = 'settled' | 'awaiting' | 'verified' | 'notLive' | 'unconfirmed';

/**
 * `verify_state` → what to draw. Pure.
 *
 * ⚠️ `notLive` and `awaiting` are separate on purpose and must never be merged.
 * `not_live` means we looked and the platform is not showing it; `pending`
 * (and `null`, its starting point) means this round could not tell us — a
 * crashed browser container looks exactly like that. Collapsing the two would
 * render our own outage as "the platform rejected your post", which is the
 * asymmetry `publish_readback.py`'s docstring is written about.
 *
 * Anything that is not a session-channel success returns `settled`: official
 * and H5 rows get their URL from the API that created them and are never read
 * back, so showing them as "awaiting confirmation" would invent a wait that is
 * not happening. This mirrors the backend's `_readback_columns`, which scopes
 * the same way.
 */
const verifyDisplayFor = (a: PublishTaskAccount): VerifyDisplay => {
  if (a.status !== 'success' || a.channel !== 'session') return 'settled';
  switch (a.verify_state) {
    case 'verified': return 'verified';
    case 'not_live': return 'notLive';
    case 'abandoned': return 'unconfirmed';
    // A platform with no read-back implementation is OUR coverage gap. Holding
    // the row in "awaiting" forever would punish the user for it, so it reads
    // as plainly published — same call the backend's `readback_verdict` makes.
    case 'not_supported': return 'settled';
    case 'pending':
    default: return 'awaiting'; // includes null/undefined = never checked yet
  }
};

/** `[reason] prose` from a read-back → the i18n key that explains it. Same
 *  split as PUBLISH_NOTE_KEYS: the bracketed reason is the contract, the
 *  English after it is written for logs and must never reach a user. */
const VERIFY_REASON_KEYS: ReadonlyArray<{ test: RegExp; key: string; fallback: string }> = [
  {
    test: /\[rejected\]/i,
    key: 'distribution.records.verifyRejected',
    fallback: 'The platform refused this post. Check the account notifications for the reason, then edit and publish again.',
  },
  {
    test: /\[under_review\]/i,
    key: 'distribution.records.verifyUnderReview',
    fallback: 'The platform is still reviewing this post, so it is not public yet.',
  },
  {
    test: /\[not_found\]/i,
    key: 'distribution.records.verifyNotFound',
    fallback: 'This post is not in the account any more — it may have been removed.',
  },
];

export const RecordsPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [tasks, setTasks] = useState<PublishTask[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // How long OUR read-back takes. Fetched rather than written down here: the
  // numbers are backend constants, and a copy in the frontend is a second
  // declaration that will drift — and, worse, drift into being told to the user
  // as though the platform had promised it.
  const [readbackTiming, setReadbackTiming] = useState<ReadbackTiming | null>(null);

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

  /** One row's `error_message` → translated copy. Reason codes first, raw-text
   *  humanization second. Never the backend's own English. */
  const noteText = useCallback((raw?: string | null): string => {
    for (const row of PUBLISH_NOTE_KEYS) {
      if (raw && row.test.test(raw)) return t(row.key, row.fallback);
    }
    return humanizeTaskError(raw).message;
  }, [t]);

  useEffect(() => { void reload(); }, [reload]);

  // Separate from `reload` (not folded into its Promise.all) so a failure here
  // can never take the records list down with it. Losing the timing costs one
  // clause of one sentence; losing the list costs the page.
  useEffect(() => {
    let alive = true;
    getReadbackTiming()
      .then((timing) => { if (alive) setReadbackTiming(timing); })
      .catch((err) => { console.error('distribution: read-back timing failed', err); });
    return () => { alive = false; };
  }, []);

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

  // 按原始 platform key 取,不是 platformFor 的显示名。
  const managerUrlFor = useCallback(
    (accountId: string): string | null => {
      const acc = accounts.find((a) => a.id === accountId);
      return acc ? (PLATFORM_MANAGER_URL[acc.platform] ?? null) : null;
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

  /** Chip copy + colour for a row whose platform standing we do know something
   *  about. `settled` returns null — the plain per-status chip already says it. */
  const verifyChip = useCallback(
    (display: VerifyDisplay): { label: string; cls: string; pulse?: boolean } | null => {
      switch (display) {
        case 'awaiting':
          return {
            label: t('distribution.records.verifyAwaiting', 'Awaiting platform confirmation'),
            cls: 'chip-amber',
            pulse: true,
          };
        case 'verified':
          return { label: t('distribution.records.verifyLive', 'Live on platform'), cls: 'chip-green' };
        case 'notLive':
          return { label: t('distribution.records.verifyNotLive', 'Not live on platform'), cls: 'chip-red' };
        case 'unconfirmed':
          // NOT "failed" and NOT "rejected": we ran out of attempts without
          // ever getting an answer. Saying more than that would be inventing a
          // verdict we never obtained.
          return { label: t('distribution.records.verifyUnconfirmed', 'Could not confirm'), cls: 'chip-amber' };
        default:
          return null;
      }
    },
    [t],
  );

  /** "we start looking after X, we give up after Y" — in minutes, from the
   *  backend's own constants. Null while they have not arrived (or could not
   *  be fetched), in which case the copy simply omits the numbers rather than
   *  substituting a guess. */
  const awaitingHint = useCallback((): string => {
    if (!readbackTiming) {
      return t(
        'distribution.records.verifyAwaitingHint',
        'The upload finished. We check the platform afterwards and update this once the post is confirmed live.',
      );
    }
    const first = Math.max(1, Math.round(readbackTiming.first_check_after_seconds / 60));
    const limit = Math.max(first, Math.round(readbackTiming.give_up_after_seconds / 60));
    return t(
      'distribution.records.verifyAwaitingHintTimed',
      'The upload finished. We start checking the platform about {{first}} minutes later and keep trying for up to {{limit}} minutes.',
      { first, limit },
    );
  }, [readbackTiming, t]);

  /** A read-back `verify_detail` → translated copy, keyed on the bracketed
   *  reason. Falls back to a neutral sentence rather than echoing the backend's
   *  English, which is written for logs. */
  const verifyReasonText = useCallback(
    (raw: string | null | undefined, display: VerifyDisplay): string => {
      for (const row of VERIFY_REASON_KEYS) {
        if (raw && row.test.test(raw)) return t(row.key, row.fallback);
      }
      return display === 'notLive'
        ? t('distribution.records.verifyNotLiveGeneric', 'The platform is not showing this post.')
        : t(
          'distribution.records.verifyUnconfirmedGeneric',
          'We could not reach the platform to confirm it. The post may well be live — check the account.',
        );
    },
    [t],
  );

  const onRetry = async (id: string, mode: 'as_scheduled' | 'now' = 'as_scheduled') => {
    try {
      await retryPublishTask(id, mode);
      addToast(
        mode === 'now'
          ? t('distribution.records.publishingNow', 'Publishing now')
          : t('distribution.records.retried', 'Retrying'),
        'success',
      );
      void reload();
    } catch (err) {
      console.error('distribution: retry failed', err);
      // `schedule_state` is a snapshot taken when the list was fetched, so a
      // page left open across the deadline still shows Retry. Pressing it now
      // lands on the backend's typed 409 — and answering that with a generic
      // "Retry failed" would throw away a reason the backend went to the
      // trouble of naming, leaving the user exactly where they started.
      // Reloading swaps the row over to "Publish now" so the next click works.
      if (isScheduleUnreachable(err)) {
        addToast(
          t(
            'distribution.records.scheduleExpired',
            'The scheduled time for this batch has passed, so it cannot be published as scheduled any more.',
          ),
          'error',
        );
        void reload();
        return;
      }
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
    { key: 'failed', label: t('distribution.records.failed', 'Failed') },
    { key: 'done', label: t('distribution.records.done', 'Completed') },
  ];

  // v4 day groups read "Today"/"Yesterday" instead of raw ISO dates.
  const dayLabel = useCallback((key: string): string => {
    const now = new Date();
    if (key === localDayKey(now)) return t('distribution.records.today', 'Today');
    const yesterday = new Date(now.getTime() - 86_400_000);
    if (key === localDayKey(yesterday)) return t('distribution.records.yesterday', 'Yesterday');
    const d = new Date(`${key}T00:00:00`);
    return Number.isNaN(d.getTime())
      ? key
      : d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
  }, [t]);

  // Collapsed multi-account cards show a per-status breakdown
  // ("2 done · 1 publishing · 1 failed") like the v4 mockup.
  const breakdownFor = useCallback((accounts: PublishTaskAccount[]): string => {
    const buckets: Record<string, number> = {};
    for (const a of accounts) {
      const k = (a.status === 'pending' || a.status === 'publishing') ? 'publishing'
        : a.status === 'pending_share' ? 'waiting'
          : a.status === 'success' ? 'done'
            : a.status === 'cancelled' ? 'cancelled' : 'failed';
      buckets[k] = (buckets[k] ?? 0) + 1;
    }
    const parts: string[] = [];
    if (buckets.done) parts.push(t('distribution.records.bdDone', '{{n}} done', { n: buckets.done }));
    if (buckets.publishing) parts.push(t('distribution.records.bdPublishing', '{{n}} publishing', { n: buckets.publishing }));
    if (buckets.waiting) parts.push(t('distribution.records.bdWaiting', '{{n}} waiting', { n: buckets.waiting }));
    if (buckets.failed) parts.push(t('distribution.records.bdFailed', '{{n}} failed', { n: buckets.failed }));
    if (buckets.cancelled) parts.push(t('distribution.records.bdCancelled', '{{n}} cancelled', { n: buckets.cancelled }));
    // A single homogeneous bucket duplicates the status chip — skip it then.
    return parts.length > 1 ? parts.join(' · ') : '';
  }, [t]);

  return (
    <div className="dist-v4">
      <PageHeader
        level="content"
        title={t('distribution.records.title', 'Publish Records')}
        count={tasks.length}
        subtitle={t('distribution.records.subtitle', 'Every publish task across accounts, live from Task Center.')}
        actions={
          <button type="button" className="btn btn-ghost btn-sm" disabled title={t('distribution.comingInD3', 'Coming in D3')}>{t('distribution.records.export', 'Export')}</button>
        }
      />

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
          <div className="day-label">{dayLabel(day)}</div>
          {dayTasks.map((task) => {
            const open = Boolean(expanded[task.id]);
            const chipMeta = STATUS_META[task.status];
            const chipLabel = taskStatusLabel(task.status);
            const singleAccount = task.accounts.length === 1 ? task.accounts[0] : null;
            const breakdown = singleAccount ? '' : breakdownFor(task.accounts);
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
                      {contentLabel}<span className="sep">·</span>{accountSummary}
                      {breakdown && (<><span className="sep">·</span>{breakdown}</>)}
                      <span className="sep">·</span>{formatTime(task.created_at)}
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

                {/* Outside the accordion on purpose. A verification code has a
                    180s window, so a prompt only visible after the user thinks
                    to expand the right row is a prompt most people never see —
                    which is the same outcome as not having built the channel.
                    Renders nothing unless a publish is genuinely parked. */}
                <PublishSmsPrompt
                  taskId={task.id}
                  active={task.status === 'publishing' || task.status === 'pending'}
                />

                {open && task.accounts.length > 0 && (
                  <div className="rec-sub">
                    {task.accounts.map((a) => {
                      const aChipMeta = ACCOUNT_STATUS_META[a.status];
                      const aChipLabel = accountStatusLabel(a.status);
                      const platform = platformFor(a.account_id);
                      // What the PLATFORM says, which is a different question
                      // from whether our upload finished (`a.status`). Only a
                      // session-channel success has one at all.
                      const vDisplay = verifyDisplayFor(a);
                      const vChip = verifyChip(vDisplay);
                      return (
                        <div key={a.id} className="sub-row">
                          {/* `avatar_url` here is joined live off social_accounts
                              (publish_task_accounts stores no copy), so a rebound
                              account shows its current picture on old records. */}
                          <AccountAvatar
                            gradient={gradientFor(a.account_id)}
                            username={a.username}
                            avatarUrl={a.avatar_url}
                          />
                          <span className="who">
                            {a.username}{platform ? ` · ${platform}` : ''}
                          </span>
                          <span className={`chip ${aChipMeta.cls} ${aChipMeta.pulse ? 'pulse' : ''}`}>
                            <span className="d" />{aChipLabel}
                          </span>
                          {/* Second chip, next to (not instead of) the upload
                              status. Both facts are true and neither implies
                              the other: we finished sending it, AND the
                              platform has/has not confirmed it. Replacing the
                              first would lose "the upload is done", which is
                              what tells the user there is nothing to re-send. */}
                          {vChip && (
                            <span className={`chip ${vChip.cls} ${vChip.pulse ? 'pulse' : ''}`}>
                              <span className="d" />{vChip.label}
                            </span>
                          )}
                          {vDisplay === 'awaiting' && (
                            <span className="note">{awaitingHint()}</span>
                          )}
                          {/* The typed reason, carried through to the pixel.
                              "Not live" on its own cannot be acted on — whether
                              to appeal, wait, or re-publish depends entirely on
                              WHY, and `unconfirmed` deliberately says something
                              different from `notLive` here (we never got an
                              answer vs. we got a negative one). */}
                          {(vDisplay === 'notLive' || vDisplay === 'unconfirmed') && (
                            <span
                              className={vDisplay === 'notLive' ? 'err' : 'note'}
                              title={a.verify_detail || undefined}
                            >
                              {verifyReasonText(a.verify_detail, vDisplay)}
                            </span>
                          )}
                          {a.status === 'success' && a.published_url && (
                            <a href={a.published_url} target="_blank" rel="noreferrer">
                              {t('distribution.records.view', 'View post')}
                            </a>
                          )}
                          {/* No direct link: the session channel cannot produce
                              one. Douyin's post-publish redirect lands on the
                              content manager and carries no item id, the post
                              cards expose neither an href nor an id attribute,
                              and no listing XHR returns one either (all three
                              verified against the live console, 2026-08-08).
                              Guessing "the newest card" would mislabel the row
                              for any account with a scheduled or concurrent
                              post — worse than no link at all.

                              So we send the user to the manager page instead,
                              and the copy says exactly that. Calling this
                              "View post" would be a lie about where it goes. */}
                          {a.status === 'success' && !a.published_url && managerUrlFor(a.account_id) && (
                            <a href={managerUrlFor(a.account_id)!} target="_blank" rel="noreferrer">
                              {t('distribution.records.openManager', 'Open in platform')}
                            </a>
                          )}
                          {/* A published row can still carry a note: the browser
                              degrades a missing collection instead of throwing
                              away a finished upload, and that degradation has to
                              be visible or it is a silent no-op. Rendered as a
                              caveat, never as a failure — the post did go out. */}
                          {a.status === 'success' && a.error_message && (
                            <span className="note" title={a.error_message}>
                              {noteText(a.error_message)}
                            </span>
                          )}
                          {a.status === 'failed' && (
                            <>
                              <span className="err" title={a.error_message || undefined}>
                                {noteText(a.error_message)}
                              </span>
                              {/* A batch keeps the `scheduled_at` it was created
                                  with, and the publish intent is re-validated
                                  before the browser opens. Once that time has
                                  passed, "Retry" is a button that CANNOT
                                  succeed — pressing it dispatches a workflow
                                  that is rejected on the same rule, writes the
                                  same failure back, and leaves the row looking
                                  untouched. (A user pressed it three times.)

                                  So we do not offer it. What we offer instead
                                  says exactly what it will do: publish now,
                                  without the schedule. That IS a change to what
                                  the user originally asked for and it cannot be
                                  undone once the post is up — which is why it
                                  is a differently-labelled button next to a
                                  sentence explaining the situation, and never
                                  something "Retry" quietly turns into. Wanting
                                  a different time is a new publish; the Publish
                                  page is where the picker (and the platform's
                                  own window) lives. */}
                              {task.schedule_state === 'unreachable' ? (
                                <>
                                  <span className="note">
                                    {t(
                                      'distribution.records.scheduleExpired',
                                      'The scheduled time for this batch has passed, so it cannot be published as scheduled any more.',
                                    )}
                                  </span>
                                  <button
                                    type="button"
                                    className="btn btn-ghost btn-sm"
                                    onClick={() => void onRetry(task.id, 'now')}
                                  >
                                    {t('distribution.records.publishNow', 'Publish now')}
                                  </button>
                                </>
                              ) : (
                                <button type="button" className="btn btn-ghost btn-sm" onClick={() => void onRetry(task.id)}>
                                  {t('distribution.records.retry', 'Retry')}
                                </button>
                              )}
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
