// components/settings/JimengCliCard.tsx
//
// Settings → AI → Local CLI → Dreamina card, IC-style (status badge +
// Log in / Check credits / Help action row + collapsible install help).
//
// Two layers on purpose: per-DEVICE dreamina state rides each paired
// device's env line above (C6 — generations run on the user's machine with
// their own credits); THIS card manages the transitional server-side shared
// account and its device-flow login.

import { Coins, HelpCircle, Loader2, QrCode, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiFetch } from '../../services/apiClient';

const BTN_PRIMARY =
  'flex items-center gap-1.5 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 text-xs font-bold disabled:opacity-50';
const BTN_SECONDARY =
  'flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs font-semibold text-content';

interface JimengStatus {
  available: boolean;
  logged_in: boolean;
  total_credit?: number | null;
  vip_level?: string;
  reason?: string;
}

export function JimengCliCard() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<JimengStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [helpOpen, setHelpOpen] = useState(false);
  const [loginInfo, setLoginInfo] = useState<{
    verification_uri?: string;
    user_code?: string | null;
    already_logged_in?: boolean;
  } | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/api/v1/jimeng-cli/status');
      const body = (await res.json()) as { data?: JimengStatus };
      setStatus(body.data ?? null);
      setError(null);
    } catch (err) {
      console.error('[jimeng-cli] status failed:', err);
      setError(t('settings.localCli.errStatus'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const startLogin = async () => {
    setWorking(true);
    setError(null);
    try {
      const res = await apiFetch('/api/v1/jimeng-cli/login', { method: 'POST' });
      const body = (await res.json()) as { data?: typeof loginInfo };
      setLoginInfo(body.data ?? null);
      if (body.data?.already_logged_in) void refresh();
    } catch (err) {
      console.error('[jimeng-cli] login failed:', err);
      setError(t('settings.localCli.errLogin'));
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="space-y-3" data-testid="jimeng-cli-card">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-bold text-content">{t('settings.localCli.jimengTitle')}</h3>
        {!loading && status && (
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
              status.logged_in ? 'bg-ok/15 text-ok' : 'bg-warn/15 text-warn'
            }`}
          >
            {status.logged_in ? t('settings.localCli.jimengLoggedIn') : t('settings.localCli.jimengNotLoggedIn')}
          </span>
        )}
        {status?.logged_in && status.total_credit != null && (
          <span className="inline-flex items-center gap-1 text-xs text-content-2">
            <Coins size={12} /> {t('settings.localCli.credits', { count: status.total_credit })}
          </span>
        )}
      </div>
      <p className="text-xs text-content-3">
        {t('settings.localCli.jimengDesc')}
      </p>

      {error && <div className="text-xs text-danger">{error}</div>}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="jimeng-login"
          onClick={() => void startLogin()}
          disabled={working}
          className={BTN_PRIMARY}
        >
          {working ? <Loader2 size={12} className="animate-spin" /> : <QrCode size={12} />}
          {t('settings.localCli.logIn')}
        </button>
        <button type="button" onClick={() => void refresh()} className={BTN_SECONDARY}>
          <RefreshCw size={12} /> {t('settings.localCli.checkCredits')}
        </button>
        <button
          type="button"
          data-testid="jimeng-help-toggle"
          onClick={() => setHelpOpen((v) => !v)}
          className={BTN_SECONDARY}
        >
          <HelpCircle size={12} /> {t('settings.localCli.help')}
        </button>
      </div>

      {helpOpen && (
        <div className="rounded-xl border border-line p-3 text-xs text-content-3">
          <div>{t('settings.localCli.jimengHelpIntro')}</div>
          <code className="mt-1 block rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs text-content-2">
            curl -fsSL https://jimeng.jianying.com/cli | bash{'\n'}dreamina login
          </code>
          <div className="mt-2">
            {t('settings.localCli.jimengHelpAfter')}
          </div>
        </div>
      )}

      {loginInfo && !loginInfo.already_logged_in && loginInfo.verification_uri && (
        <div className="rounded-xl border border-line p-3 text-xs">
          <div className="text-content-3">
            {t('settings.localCli.loginLink')}
          </div>
          <a
            href={loginInfo.verification_uri}
            target="_blank"
            rel="noreferrer"
            className="break-all font-mono text-accent"
          >
            {loginInfo.verification_uri}
          </a>
          {loginInfo.user_code && (
            <div className="mt-1 font-mono text-lg font-bold tracking-widest text-content">
              {loginInfo.user_code}
            </div>
          )}
          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-2 text-xs font-semibold text-accent"
          >
            {t('settings.localCli.loginDone')}
          </button>
        </div>
      )}
    </div>
  );
}
