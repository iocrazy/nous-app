import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useToast } from '../Toast';
import {
  connectAccount, deleteAccount, listAccounts, listPublishTasks, refreshAccount,
} from '../../services/distributionService';
import { SocialAccount, PublishTask } from '../../types';
import { PLATFORM_BADGE, PLATFORM_LABEL, gradientFor } from './platform';
import './distribution-v4.css';

const WEEK_MS = 7 * 86_400_000;

export const AccountsPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [tasks, setTasks] = useState<PublishTask[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      setAccounts(await listAccounts());
    } catch (err) {
      console.error('distribution: list accounts failed', err);
      addToast(t('distribution.loadFailed', 'Failed to load accounts'), 'error');
    } finally {
      setLoading(false);
    }
    // Stats side-channel (posts this week / per-account usage) — non-fatal.
    try {
      setTasks(await listPublishTasks());
    } catch (err) {
      console.error('distribution: list publish tasks for stats failed', err);
    }
  }, [addToast, t]);

  useEffect(() => { void reload(); }, [reload]);

  const onConnect = async () => {
    try {
      const { auth_url } = await connectAccount({
        platform: 'douyin', scope_type: 'user', scope_id: 'self',
      });
      window.location.href = auth_url;
    } catch (err) {
      console.error('distribution: connect failed', err);
      addToast(t('distribution.connectFailed', 'Could not start Douyin authorization'), 'error');
    }
  };

  const onRefresh = async (id: string) => {
    try {
      await refreshAccount(id);
      addToast(t('distribution.refreshed', 'Authorization refreshed'), 'success');
      void reload();
    } catch (err) {
      console.error('distribution: refresh failed', err);
      addToast(t('distribution.refreshFailed', 'Refresh failed — reauthorize'), 'error');
    }
  };

  const onDelete = async (id: string) => {
    try {
      await deleteAccount(id);
      void reload();
    } catch (err) {
      console.error('distribution: delete failed', err);
      addToast(t('distribution.deleteFailed', 'Failed to remove account'), 'error');
    }
  };

  // ── stats derived from real publish tasks ──
  const expiredCount = useMemo(
    () => accounts.filter((a) => a.status === 'expired').length,
    [accounts],
  );
  const platformCount = useMemo(
    () => new Set(accounts.map((a) => a.platform)).size,
    [accounts],
  );
  const postsThisWeek = useMemo(() => {
    const cutoff = Date.now() - WEEK_MS;
    return tasks
      .filter((task) => new Date(task.created_at).getTime() >= cutoff)
      .reduce((n, task) => n + task.accounts.length, 0);
  }, [tasks]);
  // account id → total posts, so cards can show honest per-account usage.
  const postsByAccount = useMemo(() => {
    const m = new Map<string, number>();
    for (const task of tasks) {
      for (const row of task.accounts) {
        m.set(row.account_id, (m.get(row.account_id) ?? 0) + 1);
      }
    }
    return m;
  }, [tasks]);

  return (
    <div className="dist-v4">
      <div className="page-head">
        <div>
          <h2>
            {t('distribution.accountsTitle', 'Platform Accounts')}
            <span className="count">{accounts.length}</span>
          </h2>
          <p>{t('distribution.accountsSubtitle', 'Connect social accounts to publish from Nous')}</p>
        </div>
        <button type="button" className="btn btn-tint-indigo" onClick={onConnect}>
          <Plus size={15} /> {t('distribution.connectAccount', 'Connect Account')}
        </button>
      </div>

      <div className="stats">
        <div className="stat">
          <div className="k">{t('distribution.statsConnected', 'Connected')}</div>
          <div className="v">
            {accounts.length}
            {' '}
            <small>{t('distribution.statsAcrossPlatforms', 'across {{n}} platforms', { n: platformCount })}</small>
          </div>
        </div>
        <div className="stat">
          <div className="k">{t('distribution.statsPostsWeek', 'Posts this week')}</div>
          <div className="v">{postsThisWeek}</div>
        </div>
        <div className={`stat ${expiredCount > 0 ? 'warn' : ''}`}>
          <div className="k">{t('distribution.statsNeedsAttention', 'Needs attention')}</div>
          <div className="v">
            {expiredCount}
            {' '}
            {expiredCount > 0 && <small>{t('distribution.statsExpiredDesc', 'authorization expired')}</small>}
          </div>
        </div>
      </div>

      {loading ? (
        <p className="text-sm" style={{ color: 'var(--content-4)' }}>{t('common.loading', 'Loading...')}</p>
      ) : (
        <div className="acct-grid">
          {accounts.map((a) => {
            const expired = a.status === 'expired';
            const badge = PLATFORM_BADGE[a.platform];
            const posts = postsByAccount.get(a.id) ?? 0;
            return (
              <div key={a.id} className={`acct ${expired ? 'warn' : ''}`}>
                <div className="acct-top">
                  <span className="ava" style={{ background: gradientFor(a.id) }}>
                    {a.username.slice(0, 2).toUpperCase()}
                    {badge && (
                      <span className="pbadge" style={{ background: badge.bg }}>{badge.icon}</span>
                    )}
                  </span>
                  <div className="acct-name">
                    <b>{a.username}</b>
                    <span>{PLATFORM_LABEL[a.platform] ?? a.platform} · {a.platform_user_id}</span>
                  </div>
                </div>
                <div className="chips">
                  {expired ? (
                    <span className="chip chip-amber"><span className="d" />{t('distribution.expired', 'Authorization expired')}</span>
                  ) : (
                    <span className="chip chip-green"><span className="d" />{t('distribution.active', 'Active')}</span>
                  )}
                  {a.scope_type === 'team' ? (
                    <span className="chip chip-violet">{t('distribution.teamScope', 'Team')}</span>
                  ) : (
                    <span className="chip chip-mute">{t('distribution.personalScope', 'Personal')}</span>
                  )}
                </div>
                <div className="acct-foot">
                  <span className="acct-meta">
                    {posts > 0
                      ? t('distribution.metaPosts', '{{n}} posts', { n: posts })
                      : t('distribution.metaNoPosts', 'No posts yet')}
                  </span>
                  {expired ? (
                    <button type="button" className="btn btn-tint-amber btn-sm" onClick={() => onRefresh(a.id)}>
                      <RefreshCw size={13} /> {t('distribution.reauthorize', 'Reauthorize')}
                    </button>
                  ) : (
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => onDelete(a.id)}>
                      <Trash2 size={13} /> {t('common.remove', 'Remove')}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
          <button type="button" className="acct ghost" onClick={onConnect}>
            <Plus size={18} />
            {t('distribution.connectAnother', 'Connect another account')}
          </button>
        </div>
      )}

      <div className="panel">
        <h3>{t('distribution.platformPanelTitle', 'Connect a platform')}</h3>
        <p className="hint">{t('distribution.platformPanelHint', 'Douyin ships first — other platforms reuse the same adapter and light up one by one.')}</p>
        <div className="plat-row">
          <button type="button" className="plat ready" onClick={onConnect}>
            <span className="pbadge lg" style={{ background: PLATFORM_BADGE.douyin.bg }}>{PLATFORM_BADGE.douyin.icon}</span>
            <div>Douyin<small>{t('distribution.platReady', 'Ready')}</small></div>
          </button>
          <div className="plat soon">
            <span className="pbadge lg" style={{ background: PLATFORM_BADGE.kuaishou.bg }}>{PLATFORM_BADGE.kuaishou.icon}</span>
            <div>Kuaishou<small>{t('distribution.platPlanned', 'Planned')}</small></div>
          </div>
          <div className="plat soon">
            <span className="pbadge lg" style={{ background: PLATFORM_BADGE.xiaohongshu.bg }}>{PLATFORM_BADGE.xiaohongshu.icon}</span>
            <div>Xiaohongshu<small>{t('distribution.platPlanned', 'Planned')}</small></div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AccountsPage;
