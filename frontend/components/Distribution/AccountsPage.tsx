import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { PageHeader } from '../AILibrary/PageHeader';
import { useToast } from '../Toast';
import {
  connectAccount, deleteAccount, listAccounts, refreshAccount,
} from '../../services/distributionService';
import { SocialAccount } from '../../types';

const PLATFORM_LABEL: Record<string, string> = {
  douyin: 'Douyin', kuaishou: 'Kuaishou', xiaohongshu: 'Xiaohongshu',
};

export const AccountsPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
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

  return (
    <div className="pt-6">
      <PageHeader
        title={t('distribution.accountsTitle', 'Platform Accounts')}
        count={accounts.length}
        subtitle={t('distribution.accountsSubtitle', 'Connect social accounts to publish from MediaHub')}
        actions={
          <button
            onClick={onConnect}
            className="btn-tint-indigo flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-[13px] font-medium"
          >
            <Plus size={15} /> {t('distribution.connectAccount', 'Connect Account')}
          </button>
        }
      />
      {loading ? (
        <p className="text-sm text-ink-500">{t('common.loading', 'Loading...')}</p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
          {accounts.map((a) => (
            <div key={a.id} className="rounded-2xl border border-ink-800 bg-ink-900/60 p-4 flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-full bg-gradient-to-br from-sky-500 to-indigo-500 flex items-center justify-center text-sm font-semibold text-white">
                  {a.username.slice(0, 2).toUpperCase()}
                </div>
                <div className="min-w-0">
                  <p className="truncate text-[13.5px] font-semibold text-ink-100">{a.username}</p>
                  <p className="text-[11px] text-ink-600">{PLATFORM_LABEL[a.platform] ?? a.platform}</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 text-[11px]">
                {a.status === 'active' ? (
                  <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-emerald-300">
                    {t('distribution.active', 'Active')}
                  </span>
                ) : (
                  <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-amber-300">
                    {t('distribution.expired', 'Authorization expired')}
                  </span>
                )}
                <span className="rounded-full border border-ink-700 px-2 py-0.5 text-ink-400">
                  {a.scope_type === 'team'
                    ? t('distribution.teamScope', 'Team')
                    : t('distribution.personalScope', 'Personal')}
                </span>
              </div>
              <div className="mt-auto flex items-center justify-between border-t border-ink-800 pt-2.5">
                {a.status === 'expired' ? (
                  <button onClick={() => onRefresh(a.id)}
                    className="btn-tint-amber flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px]">
                    <RefreshCw size={13} /> {t('distribution.reauthorize', 'Reauthorize')}
                  </button>
                ) : <span />}
                <button onClick={() => onDelete(a.id)}
                  className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-ink-500 hover:text-red-400">
                  <Trash2 size={13} /> {t('common.remove', 'Remove')}
                </button>
              </div>
            </div>
          ))}
          <button onClick={onConnect}
            className="flex min-h-[150px] flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-ink-700 text-[12.5px] text-ink-500 hover:border-indigo-500/50 hover:text-indigo-300">
            <Plus size={18} /> {t('distribution.connectAnother', 'Connect another account')}
          </button>
        </div>
      )}
    </div>
  );
};

export default AccountsPage;
