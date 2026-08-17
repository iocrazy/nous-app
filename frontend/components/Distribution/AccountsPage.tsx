import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getSupabaseClient } from '../../supabaseClient';
import {
  AlertTriangle, KeyRound, Plus, QrCode, RefreshCw, ShieldCheck, ShieldQuestion, Trash2, X,
} from 'lucide-react';
import { useToast } from '../Toast';
import {
  connectAccount, deleteAccount, getAccountUsage, getBrowserHealth, listAccounts,
  listPublishTasks, refreshAccount,
} from '../../services/distributionService';
import { SocialAccount, PublishTask } from '../../types';
import { AccountAvatar, PLATFORM_BADGE, PLATFORM_LABEL, gradientFor } from './platform';
import { describeSessionFreshness, needsReconnect, type SessionFreshness } from './accountStatus';
import { PageHeader } from '../layout/PageHeader';
import { useConfirm } from '../ConfirmDialog';
import SessionLoginModal from './SessionLoginModal';
import './distribution-v4.css';

const WEEK_MS = 7 * 86_400_000;

/**
 * What we know about the browser service every QR binding runs inside (D1).
 *
 * Three states, not a boolean, because "we could not ask" is not "it is down".
 * `unknown` keeps the button live: the gate exists to replace a guaranteed
 * failure with an upfront sentence, and a probe whose own request failed is no
 * evidence about the browser container — blocking on it would invent an outage
 * out of a flaky call. `down` is only ever set from a completed probe that
 * came back `ok: false`, so there is no path to "unavailable" without one, and
 * (the mirror image) no hardcoded healthy path either.
 */
type BrowserGate = 'unknown' | 'ok' | 'down';

/** Which binding flow to start — chosen in the Connect modal. */
type SessionLoginTarget = {
  platform: string;
  scopeType: 'user' | 'team';
  scopeId: string;
  relinkUsername?: string;
};

/**
 * "When did we last confirm this account works" — stated on the card.
 *
 * The status chip above answers a different question. `Active` is the absence
 * of a *failure*, and until this line existed it looked identical on an account
 * verified an hour ago and on one the sweep had never reached: no evidence
 * rendered as a positive verdict. So the two cases are separated on three axes
 * at once — icon, tone class and wording — rather than by a single adjective a
 * user would have to notice.
 *
 * What it deliberately does NOT do is judge. There is no "stale" threshold and
 * no colour that means "too old": the sweep's recheck interval lives in the
 * backend and is env-overridable, so any cutoff written here would be a guess
 * wearing the clothes of a fact. Age is reported; the reader decides.
 *
 * OAuth accounts render nothing — see `describeSessionFreshness`.
 */
const SessionCheckLine: React.FC<{ freshness: SessionFreshness }> = ({ freshness }) => {
  const { t } = useTranslation();
  if (freshness.kind === 'notApplicable') return null;

  const unverified = freshness.kind !== 'checked';
  const label = (() => {
    switch (freshness.kind) {
      case 'never':
        return t('distribution.checkNever', 'Not checked yet — sign-in state unconfirmed');
      case 'unknown':
        return t('distribution.checkUnknown', 'Last check time unavailable');
      default:
        switch (freshness.unit) {
          case 'now':
            return t('distribution.checkJustNow', 'Checked just now');
          case 'minutes':
            return t('distribution.checkMinutes', 'Checked {{n}}m ago', { n: freshness.value });
          case 'hours':
            return t('distribution.checkHours', 'Checked {{n}}h ago', { n: freshness.value });
          default:
            return t('distribution.checkDays', 'Checked {{n}}d ago', { n: freshness.value });
        }
    }
  })();

  return (
    <div className={`acct-check${unverified ? ' unverified' : ''}`} data-testid="acct-check">
      {unverified ? <ShieldQuestion size={12} /> : <ShieldCheck size={12} />}
      <span>{label}</span>
    </div>
  );
};

export const AccountsPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const confirm = useConfirm();
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [tasks, setTasks] = useState<PublishTask[]>([]);
  const [loading, setLoading] = useState(true);
  // Both binding entry points funnel through here: pick a method, then run it.
  const [methodPicker, setMethodPicker] = useState<string | null>(null);
  const [sessionLogin, setSessionLogin] = useState<SessionLoginTarget | null>(null);
  const [browserGate, setBrowserGate] = useState<BrowserGate>('unknown');
  const [checkingBrowser, setCheckingBrowser] = useState(false);

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

  /**
   * Ask the backend whether the browser service can take a QR login right now,
   * and return the verdict so a click can act on it without waiting for state.
   *
   * Called exactly three ways — on mount, immediately before a login actually
   * starts, and from the notice's Check again button. **No interval.** The
   * probe launches a real Chromium on the other side; a page left open on a
   * second monitor must not keep doing that, and the mount check plus the
   * pre-click re-check already cover both moments where the answer changes
   * anything (the page-load one is what disables the button; the pre-click one
   * is what catches a service that went down while the page sat idle).
   */
  const probeBrowser = useCallback(async (): Promise<BrowserGate> => {
    setCheckingBrowser(true);
    try {
      const health = await getBrowserHealth();
      const gate: BrowserGate = health.ok ? 'ok' : 'down';
      if (!health.ok) {
        console.warn(
          'distribution: browser service unhealthy',
          health.error_kind, health.message,
        );
      }
      setBrowserGate(gate);
      return gate;
    } catch (err) {
      // The probe call itself failed (our API, not the browser container).
      // Fall back to `unknown` — see the BrowserGate note: refusing to bind on
      // no evidence would be a worse lie than letting the click through.
      console.error('distribution: browser health probe failed', err);
      setBrowserGate('unknown');
      return 'unknown';
    } finally {
      setCheckingBrowser(false);
    }
  }, []);

  useEffect(() => { void probeBrowser(); }, [probeBrowser]);

  // ── Realtime:账号行变化直接推过来 ──────────────────────────────
  //
  // 用户两次反馈同一件事:扫码绑定成功了,卡片却不出现,手动刷新才看到
  // (2026-08-06 抖音、2026-08-08 B站)。
  //
  // 后端是清白的 —— workflow 先入库、成功后才把状态写成 success,所以前端
  // 看到 success 时账号行已经在库里。缺的是**这个列表没有任何实时信号**:
  // 它只在挂载时拉一次,之后完全静止,刷新全靠 onBound/onClose 那一次
  // reload。而那两条走的是**同一个 HTTP 请求** —— 网络抖一下(用户截图里
  // 就有一批 503),两条一起失败,列表就永远停在旧数据。
  //
  // 这条订阅是一条**独立的**信号通道,不是把重试做得更狠。migration 412
  // 把 social_accounts 加进了 supabase_realtime publication。
  //
  // 收到事件只触发重新拉列表,不直接用 payload 里的行:REPLICA IDENTITY 是
  // default(仅主键),payload 不完整;而且这张表存着加密凭证,拿它当数据源
  // 会诱使以后有人去读那些字段。
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!supabase) return undefined;

    const channel = supabase
      .channel('distribution-accounts')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'social_accounts' },
        () => { void reload(); },
      )
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, [reload]);

  /**
   * Every "connect" affordance opens the method picker rather than jumping
   * straight to OAuth: the two channels differ in what they can do (only a
   * session account publishes unattended / picks its own account server-side)
   * and Douyin's official capability review has not passed, so silently
   * choosing OAuth for the user would pick the weaker one.
   */
  const onConnect = (platform = 'douyin') => setMethodPicker(platform);

  const startOAuth = async (platform: string) => {
    setMethodPicker(null);
    try {
      const { auth_url } = await connectAccount({
        platform, scope_type: 'user', scope_id: 'self',
      });
      window.location.href = auth_url;
    } catch (err) {
      console.error('distribution: connect failed', err);
      addToast(t('distribution.connectFailed', 'Could not start Douyin authorization'), 'error');
    }
  };

  /**
   * Open the QR modal only if the browser service can actually serve it.
   *
   * The disabled button covers the state we knew about at page load; this
   * re-probe covers the far more likely one — the page has been open for a
   * while and `nous-browser` restarted underneath it (a backend deploy does
   * exactly that). Without it the gate would be honest only for the first few
   * seconds of a session.
   *
   * Both refusal branches say why (CLAUDE.md「触发路径必须类型化失败回显」):
   * the modal not opening with no explanation would be the silent no-op this
   * whole change is here to remove.
   */
  const startQrLogin = async (target: SessionLoginTarget) => {
    if (await probeBrowser() === 'down') {
      addToast(
        t('distribution.browserUnavailable',
          'Connection service is temporarily unavailable — QR sign-in cannot start right now.'),
        'error',
      );
      return;
    }
    setSessionLogin(target);
  };

  const startSession = (platform: string) => {
    setMethodPicker(null);
    void startQrLogin({ platform, scopeType: 'user', scopeId: 'self' });
  };

  /** Re-link a dead browser session — same modal, account-scoped copy. */
  const onRelogin = (a: SocialAccount) => void startQrLogin({
    platform: a.platform,
    scopeType: a.scope_type,
    scopeId: a.scope_id,
    relinkUsername: a.username,
  });

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

  /**
   * Unbind, but only after showing what it costs — measured, not guessed.
   *
   * This button used to go straight to the API. The backend hard-deleted the
   * row, and `publish_task_accounts.account_id` is `ON DELETE CASCADE`, so one
   * click could take an account's whole publish history with it and say
   * nothing. Measured on prod the day this was found: of the two cards on
   * screen, one carried 10 publish records and the other 0 — the user happened
   * to click the 0 one.
   *
   * Two things changed. The backend keeps the records (soft delete), so the
   * copy is "unbind", not "delete". And the count comes from the server on
   * every open — never from `postsByAccount`, which is derived from the last
   * 100 tasks the stats call happened to return and would understate the real
   * figure exactly when it matters most.
   *
   * A failed count ABORTS instead of falling back to a confirm without a
   * number: the whole point is that the user is told the impact, and "we could
   * not check" is not that. Unbinding is never urgent, and the typed toast
   * says what to do (CLAUDE.md「触发路径必须类型化失败回显」 — a silent no-op
   * here would be the same class of bug).
   */
  const onDelete = async (a: SocialAccount) => {
    let records: number;
    try {
      ({ publish_records: records } = await getAccountUsage(a.id));
    } catch (err) {
      console.error('distribution: account usage lookup failed', err);
      addToast(
        t('distribution.unbindCheckFailed',
          'Could not check what this account is used by — not unbinding. Try again.'),
        'error',
      );
      return;
    }

    const ok = await confirm({
      variant: 'warning',
      title: t('distribution.unbindTitle', 'Unbind {{name}}?', { name: a.username }),
      message: t(
        'distribution.unbindMessage',
        'You will need to sign in again to publish from this account. Its {{n}} publish records are kept.',
        { n: records },
      ),
      confirmLabel: t('distribution.unbindConfirm', 'Unbind'),
      cancelLabel: t('common.cancel', 'Cancel'),
    });
    if (!ok) return;

    try {
      await deleteAccount(a.id);
      void reload();
    } catch (err) {
      console.error('distribution: delete failed', err);
      addToast(t('distribution.deleteFailed', 'Failed to remove account'), 'error');
    }
  };

  // ── stats derived from real publish tasks ──
  const expiredCount = useMemo(
    () => accounts.filter(needsReconnect).length,
    [accounts],
  );
  // Split so the stat can say *which* kind of attention is needed — "3
  // authorizations expired" and "3 sessions need a new scan" are different jobs.
  const reloginCount = useMemo(
    () => accounts.filter((a) => a.status === 'needs_relogin').length,
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
      <PageHeader
        level="content"
        title={t('distribution.accountsTitle', 'Platform Accounts')}
        count={accounts.length}
        subtitle={t('distribution.accountsSubtitle', 'Connect social accounts to publish from Nous')}
        actions={
          <button type="button" className="btn btn-tint-indigo" onClick={() => onConnect()}>
            <Plus size={15} /> {t('distribution.connectAccount', 'Connect Account')}
          </button>
        }
      />

      {/* The one place the outage is stated in full. The buttons below only
          go grey and carry a tooltip; without this line a user would be left
          guessing whether the app is broken or they are missing a permission.
          Manual retry, no timer — see `probeBrowser`. */}
      {browserGate === 'down' && (
        <div className="svc-notice" role="status">
          <AlertTriangle size={16} className="svc-ic" />
          <div className="svc-body">
            <b>{t('distribution.browserDownTitle', 'Connection service is temporarily unavailable')}</b>
            <p>
              {t('distribution.browserDownDesc',
                'QR sign-in runs in a browser service that is not responding — usually a restart during a deploy. Official Authorization is unaffected.')}
            </p>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => { void probeBrowser(); }}
            disabled={checkingBrowser}
          >
            <RefreshCw size={13} />
            {checkingBrowser
              ? t('distribution.browserChecking', 'Checking...')
              : t('distribution.browserRecheck', 'Check again')}
          </button>
        </div>
      )}

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
            {expiredCount > 0 && (
              <small>
                {reloginCount === expiredCount
                  ? t('distribution.statsReloginDesc', 'sessions need a new scan')
                  : reloginCount > 0
                    ? t('distribution.statsMixedDesc', 'expired or signed out')
                    : t('distribution.statsExpiredDesc', 'authorization expired')}
              </small>
            )}
          </div>
        </div>
      </div>

      {loading ? (
        <p className="text-sm" style={{ color: 'var(--content-4)' }}>{t('common.loading', 'Loading...')}</p>
      ) : (
        <div className="acct-grid">
          {accounts.map((a) => {
            const needsRelogin = a.status === 'needs_relogin';
            const expired = needsReconnect(a);
            const isSession = a.auth_type === 'session';
            const badge = PLATFORM_BADGE[a.platform];
            const posts = postsByAccount.get(a.id) ?? 0;
            return (
              <div key={a.id} className={`acct ${expired ? 'warn' : ''}`}>
                <div className="acct-top">
                  <AccountAvatar
                    gradient={gradientFor(a.id)}
                    username={a.username}
                    avatarUrl={a.avatar_url}
                  >
                    {badge && (
                      <span className="pbadge" style={{ background: badge.bg }}>{badge.icon}</span>
                    )}
                  </AccountAvatar>
                  <div className="acct-name">
                    <b>{a.username}</b>
                    <span>{PLATFORM_LABEL[a.platform] ?? a.platform} · {a.platform_user_id}</span>
                  </div>
                </div>
                <div className="chips">
                  {needsRelogin ? (
                    <span className="chip chip-warn"><span className="d" />{t('distribution.needsRelogin', 'Session signed out')}</span>
                  ) : expired ? (
                    <span className="chip chip-amber"><span className="d" />{t('distribution.expired', 'Authorization expired')}</span>
                  ) : (
                    <span className="chip chip-green"><span className="d" />{t('distribution.active', 'Active')}</span>
                  )}
                  {/* Binding method is not cosmetic: only a session account can
                      publish unattended, and the two fail (and recover) in
                      completely different ways. */}
                  {isSession ? (
                    <span
                      className="chip chip-info"
                      title={t('distribution.authSessionHint', 'Browser session — can publish unattended on a schedule')}
                    >
                      <QrCode size={11} />{t('distribution.authSession', 'QR session')}
                    </span>
                  ) : (
                    <span
                      className="chip chip-mute"
                      title={t('distribution.authOauthHint', 'Official authorization — the app must be open on your phone to finish a post')}
                    >
                      <KeyRound size={11} />{t('distribution.authOauth', 'Official')}
                    </span>
                  )}
                  {a.scope_type === 'team' ? (
                    <span className="chip chip-violet">{t('distribution.teamScope', 'Team')}</span>
                  ) : (
                    <span className="chip chip-mute">{t('distribution.personalScope', 'Personal')}</span>
                  )}
                </div>
                <SessionCheckLine freshness={describeSessionFreshness(a)} />
                <div className="acct-foot">
                  <span className="acct-meta">
                    {posts > 0
                      ? t('distribution.metaPosts', '{{n}} posts', { n: posts })
                      : t('distribution.metaNoPosts', 'No posts yet')}
                  </span>
                  {/* Recovery differs by binding method — an OAuth token is
                      refreshed at the platform, a dead session can only be
                      revived by scanning a new QR code. */}
                  {needsRelogin ? (
                    // Re-scanning is a QR binding like any other, so it is
                    // gated the same way — a dead-session card is exactly
                    // where a user clicks hardest during an outage.
                    <button
                      type="button"
                      className="btn btn-tint-amber btn-sm"
                      onClick={() => onRelogin(a)}
                      disabled={browserGate === 'down'}
                      title={browserGate === 'down'
                        ? t('distribution.browserDownTitle', 'Connection service is temporarily unavailable')
                        : undefined}
                    >
                      <QrCode size={13} /> {t('distribution.rescan', 'Scan again')}
                    </button>
                  ) : expired ? (
                    <button type="button" className="btn btn-tint-amber btn-sm" onClick={() => onRefresh(a.id)}>
                      <RefreshCw size={13} /> {t('distribution.reauthorize', 'Reauthorize')}
                    </button>
                  ) : (
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => void onDelete(a)}>
                      <Trash2 size={13} /> {t('distribution.unbind', 'Unbind')}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
          <button type="button" className="acct ghost" onClick={() => onConnect()}>
            <Plus size={18} />
            {t('distribution.connectAnother', 'Connect another account')}
          </button>
        </div>
      )}

      <div className="panel">
        <h3>{t('distribution.platformPanelTitle', 'Connect a platform')}</h3>
        <p className="hint">{t('distribution.platformPanelHint', 'Each platform can be bound two ways, and they do not ship together — other platforms reuse the same adapters and light up one by one.')}</p>
        <div className="plat-row">
          {/* "Ready" used to mean "OAuth is wired up", which overstated it:
              Douyin's publishing capability review has not passed, so the
              official channel can only hand a post off to the phone. State
              each method's real availability instead of one blanket badge. */}
          <button type="button" className="plat ready" onClick={() => onConnect('douyin')}>
            <span className="pbadge lg" style={{ background: PLATFORM_BADGE.douyin.bg }}>{PLATFORM_BADGE.douyin.icon}</span>
            <div>
              Douyin
              <small className="plat-methods">
                <span className="pm ok">
                  <QrCode size={10} /> {t('distribution.platQrReady', 'QR sign-in ready')}
                </span>
                <span className="pm warn">
                  <KeyRound size={10} /> {t('distribution.platOauthLimited', 'Official: hand-off only')}
                </span>
              </small>
            </div>
          </button>
          {/* 小红书 / B 站:能绑账号、能保活会话,但**发布未实现**。
              这两件事必须分开说 —— 只标 "QR sign-in ready" 会让人以为绑完
              就能发,而实际提交会被后端类型化拒绝。宁可这里写得啰嗦。 */}
          <button type="button" className="plat ready" onClick={() => onConnect('xiaohongshu')}>
            <span className="pbadge lg" style={{ background: PLATFORM_BADGE.xiaohongshu.bg }}>{PLATFORM_BADGE.xiaohongshu.icon}</span>
            <div>
              Xiaohongshu
              <small className="plat-methods">
                <span className="pm ok">
                  <QrCode size={10} /> {t('distribution.platQrReady', 'QR sign-in ready')}
                </span>
                <span className="pm warn">
                  {t('distribution.platNoPublish', 'Publishing not available yet')}
                </span>
              </small>
            </div>
          </button>
          <button type="button" className="plat ready" onClick={() => onConnect('bilibili')}>
            <span className="pbadge lg" style={{ background: PLATFORM_BADGE.bilibili.bg }}>{PLATFORM_BADGE.bilibili.icon}</span>
            <div>
              Bilibili
              <small className="plat-methods">
                <span className="pm ok">
                  <QrCode size={10} /> {t('distribution.platQrReady', 'QR sign-in ready')}
                </span>
                <span className="pm warn">
                  {t('distribution.platNoPublish', 'Publishing not available yet')}
                </span>
              </small>
            </div>
          </button>
          <div className="plat soon">
            <span className="pbadge lg" style={{ background: PLATFORM_BADGE.kuaishou.bg }}>{PLATFORM_BADGE.kuaishou.icon}</span>
            <div>Kuaishou<small>{t('distribution.platPlanned', 'Planned')}</small></div>
          </div>
        </div>
      </div>

      {methodPicker && (
        <div className="picker-overlay" role="presentation" onClick={() => setMethodPicker(null)}>
          <div
            className="picker method"
            role="dialog"
            aria-modal="true"
            aria-label={t('distribution.methodTitle', 'Choose how to connect')}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="picker-head">
              <div>
                <h3>{t('distribution.methodTitle', 'Choose how to connect')}</h3>
                <p>
                  {t('distribution.methodSubtitle', 'Both bind a {{platform}} account, but they can do different things.', {
                    platform: PLATFORM_LABEL[methodPicker] ?? methodPicker,
                  })}
                </p>
              </div>
              <button
                type="button"
                className="picker-close"
                aria-label={t('distribution.methodClose', 'Close')}
                onClick={() => setMethodPicker(null)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="method-row">
              {/* Only this half is gated: OAuth is a redirect to the
                  platform's own page and never touches nous-browser, so
                  greying both out during an outage would remove a channel
                  that still works. */}
              <button
                type="button"
                className="method-card"
                onClick={() => startSession(methodPicker)}
                disabled={browserGate === 'down'}
              >
                <span className="mc-ic tone-info"><QrCode size={17} /></span>
                <b>{t('distribution.methodSession', 'QR Code Login')}</b>
                <span>{t('distribution.methodSessionDesc', 'Scan once with the app. Publishes unattended on a schedule and picks the account server-side.')}</span>
                {browserGate === 'down' ? (
                  <em className="tone-warn">
                    {t('distribution.browserDownTitle', 'Connection service is temporarily unavailable')}
                  </em>
                ) : (
                  <em className="tone-ok">{t('distribution.methodSessionNote', 'Recommended for matrix accounts')}</em>
                )}
              </button>
              <button type="button" className="method-card" onClick={() => void startOAuth(methodPicker)}>
                <span className="mc-ic tone-warn"><KeyRound size={17} /></span>
                <b>{t('distribution.methodOauth', 'Official Authorization')}</b>
                <span>{t('distribution.methodOauthDesc', 'Sign in on the platform’s own page. Nothing to maintain, but every post is finished by hand in the app.')}</span>
                <em className="tone-warn">{t('distribution.methodOauthNote', 'No unattended publishing yet')}</em>
              </button>
            </div>
          </div>
        </div>
      )}

      {sessionLogin && (
        <SessionLoginModal
          platform={sessionLogin.platform}
          scopeType={sessionLogin.scopeType}
          scopeId={sessionLogin.scopeId}
          relinkUsername={sessionLogin.relinkUsername}
          // Reload on BOTH paths, deliberately.
          //
          // `onBound` fires the moment the modal sees `status: success`, which
          // is the fast path and the one that makes the new card appear while
          // the modal is still up. But it is one event on one websocket: miss
          // it — a dropped frame, a re-subscribe, the user closing the dialog a
          // beat early — and the list silently keeps showing the old accounts,
          // which is exactly what a user reported (bound fine, appeared only
          // after a manual refresh). The backend was in the clear: the account
          // row is committed ~200ms BEFORE success is broadcast, so any reload
          // after that point sees it.
          //
          // Closing is the last moment we can still fix that for free, and a
          // second list fetch costs one request against a page the user is
          // already looking at.
          onClose={() => { setSessionLogin(null); void reload(); }}
          onBound={() => { void reload(); }}
        />
      )}
    </div>
  );
};

export default AccountsPage;
