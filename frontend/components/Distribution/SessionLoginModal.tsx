import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle, CheckCircle2, Loader2, QrCode, RefreshCw, ShieldAlert, Smartphone, X,
} from 'lucide-react';
import { getSupabaseClient } from '../../supabaseClient';
import {
  SMS_CODE_PATTERN, cancelSessionLogin, startSessionLogin, submitSmsCode,
} from '../../services/distributionService';
import { SessionLoginState, SessionLoginStatus } from '../../types';
import { PLATFORM_LABEL } from './platform';

/**
 * QR-code account binding (session channel).
 *
 * Data flow, deliberately: REST only *starts* / *answers* / *cancels* the
 * login. The QR image and every subsequent state change arrive over Supabase
 * Realtime from `task_tracking.metadata.login` — the same channel the Task
 * Center already uses. No SSE, no polling.
 *
 * Two consequences the code has to respect:
 *
 * 1. The workflow writes the QR frame within a second or two of the POST
 *    returning, which can land *before* the WebSocket finishes joining. So
 *    on SUBSCRIBED we read the row once to seed state (same backstop shape
 *    as TaskManagerContext's refreshTasks-on-SUBSCRIBED).
 * 2. A pending login owns a live browser context in the nous-browser
 *    container. Closing the modal without DELETEing leaves it spinning until
 *    the server-side timeout, so every non-terminal exit path cancels.
 */

/**
 * Local-only phases. Three precede the first `metadata.login` write;
 * `session_ended` is what a 409 from /sms means — the task went terminal (TTL,
 * cancel, failure) while the form sat open, which is the one path where the
 * user finds out by acting rather than by watching.
 */
type ViewStatus =
  | SessionLoginStatus
  | 'starting' | 'connecting' | 'start_failed' | 'start_unavailable' | 'session_ended';

/** Statuses after which the workflow is done and there is nothing to cancel. */
const TERMINAL: ReadonlySet<ViewStatus> = new Set<ViewStatus>([
  'success', 'timeout', 'failed', 'proxy_failed', 'session_ended',
]);

/**
 * States where "get a new code" is the right next action — i.e. where a retry
 * can plausibly succeed.
 *
 * `start_unavailable` is deliberately absent: a 503 means the server has no
 * BROWSER_SERVICE_URL / token configured, so retrying is guaranteed to fail
 * and a button inviting it just farms clicks on a problem the user cannot
 * solve. `success` is absent for the obvious reason.
 */
const RETRYABLE: ReadonlySet<ViewStatus> = new Set<ViewStatus>([
  'start_failed', 'qrcode_expired', 'timeout', 'failed', 'proxy_failed', 'session_ended',
]);

/**
 * `detail.reason` for "the sign-in worked, but the identity cookie was never
 * in the jar" (browser-side `IdentityUnresolved`, surfaced as a typed 409 and
 * carried through `login_metadata`'s `detail` whitelist).
 *
 * It arrives as a plain `status: 'failed'` with **no** `error_kind` — byte for
 * byte the shape of a genuine platform refusal — so without this branch it
 * reads as "the platform refused the sign-in ... check whether the account is
 * restricted" and sends the user hunting for a ban that never happened. The
 * account is fine, the platform said yes, and nothing was linked: the remedy
 * is to bind again.
 */
const IDENTITY_UNRESOLVED = 'identity_unresolved';

/** An i18n key with the English it falls back to. */
type Copy = [key: string, fallback: string];

/**
 * What the detail line says, per server status. We translate it; we never echo
 * `metadata.login.message`.
 *
 * That field is **not** platform copy — it is our own machine-authored English
 * (`LoginJudgement.reason`, e.g. `browser/app/platforms/douyin.py`'s "the
 * platform is asking for a verification code"), carried verbatim through
 * `login_metadata`. Printing it under a Chinese UI is how a bare English
 * sentence ended up on the SMS screen. The status it rides on is already
 * typed, so the copy comes from i18n and the English never has to be shown.
 *
 * Keyed by status rather than by matching the sentence: the wording belongs to
 * the browser service and may be reworded there at any time — a string match
 * would silently start leaking English again, while the status is contract.
 *
 * **Total over `SessionLoginStatus`, deliberately.** A `Partial` map lets a
 * newly added status fall through to the echo path, and the failure mode of
 * that is invisible: an English sentence appears under a Chinese UI and no
 * test goes red. Being total moves the failure to compile time.
 *
 * ## The two statuses that carry a Chinese tail, and why the tail is dropped
 *
 * `scanned` and `qrcode_expired` arrive as `"scanned; waiting for confirmation
 * on the phone: 扫码成功"` — English prose plus a Chinese fragment. The
 * fragment is **not** something the platform told us: it is whichever entry of
 * our own `SCANNED_MARKERS` / `EXPIRED_MARKERS` tuple matched on the page
 * (`browser/app/platforms/douyin.py`). So it restates the status in Chinese and
 * adds nothing a user can act on, while "i18n prefix + raw tail" would produce
 * a hybrid line that reads like a debug string in both locales.
 *
 * Dropping it costs nothing even for support: the *full* original sentence,
 * tail included, still arrives in `login.detail.reason` (the browser service
 * records the judge's reason there, and `login_metadata`'s whitelist keeps it).
 * This is a decision about what to **render**, not about what to keep.
 */
export const SERVER_DETAIL_COPY: Record<SessionLoginStatus, Copy> = {
  waiting_scan: [
    'distribution.session.waitingScanDetail',
    'The sign-in page is showing a QR code.',
  ],
  scanned: [
    'distribution.session.scannedDetail',
    'The platform registered the scan and is waiting for you to approve it.',
  ],
  qrcode_expired: [
    'distribution.session.qrcodeExpiredDetail',
    'The platform marked this code as expired, so a fresh one was requested.',
  ],
  sms_required: [
    'distribution.session.smsRequiredDetail',
    'The platform is asking for a verification code.',
  ],
  success: [
    'distribution.session.successDetail',
    "The platform's creator console loaded — the sign-in is complete.",
  ],
  timeout: [
    'distribution.session.timeoutDetail',
    'The browser session for this sign-in has ended.',
  ],
  failed: [
    'distribution.session.failedDetail',
    'The browser session ended without a completed sign-in.',
  ],
  proxy_failed: [
    'distribution.session.proxyFailedDetail',
    "The browser could not reach the platform through this account's proxy.",
  ],
};

/**
 * What the SMS form says when a submission comes back `success: false`, per
 * status.
 *
 * Separate from `SERVER_DETAIL_COPY` because it answers a different question.
 * That map describes *the page*; this one describes *what happened to the code
 * you just sent* — "The browser session for this sign-in has ended" is a fine
 * status line and a useless error under an input the user is still holding.
 *
 * Total for the same reason as above, and because the alternative is what this
 * replaces: `res.message` rendered raw, which put machine English directly in
 * front of the user on every path that was not `error_kind` or `code_rejected`.
 */
export const SMS_SUBMIT_COPY: Record<SessionLoginStatus, Copy> = {
  // Reachable only if the marker ever goes missing; `code_rejected` is checked
  // first and is what a refused code actually looks like (see the SMS handler).
  sms_required: [
    'distribution.session.smsStillRequired',
    'The platform is still asking for a verification code.',
  ],
  // The page moved past the code step. Not an error in itself — say so rather
  // than implying the digits were wrong.
  waiting_scan: [
    'distribution.session.smsNoLongerNeeded',
    'The platform is no longer asking for a code — this sign-in has moved on.',
  ],
  scanned: [
    'distribution.session.smsNoLongerNeeded',
    'The platform is no longer asking for a code — this sign-in has moved on.',
  ],
  success: [
    'distribution.session.smsNoLongerNeeded',
    'The platform is no longer asking for a code — this sign-in has moved on.',
  ],
  qrcode_expired: [
    'distribution.session.smsQrExpired',
    'The QR code expired before the code was accepted — get a new one and scan again.',
  ],
  timeout: [
    'distribution.session.smsSessionEnded',
    'This sign-in ended before the code could be used — get a new code and start over.',
  ],
  proxy_failed: [
    'distribution.session.smsProxyFailed',
    "The code could not be sent — this account's proxy is unreachable.",
  ],
  failed: [
    'distribution.session.smsFailed',
    'That code was rejected — check it and try again',
  ],
};

/** Semantic tone per state — drives the dot/border color, never a hue name. */
const TONE: Record<ViewStatus, 'info' | 'ok' | 'warn' | 'danger'> = {
  starting: 'info',
  connecting: 'info',
  start_failed: 'danger',
  start_unavailable: 'danger',
  session_ended: 'warn',
  waiting_scan: 'info',
  scanned: 'info',
  qrcode_expired: 'warn',
  sms_required: 'warn',
  success: 'ok',
  timeout: 'warn',
  failed: 'danger',
  proxy_failed: 'danger',
};

export interface SessionLoginModalProps {
  platform: string;
  scopeType: 'user' | 'team';
  scopeId: string;
  /** Set when re-linking a dead session — only changes the copy. */
  relinkUsername?: string;
  onClose: () => void;
  /** Fired once the account is linked, so the caller can reload its list. */
  onBound: () => void;
}

export const SessionLoginModal: React.FC<SessionLoginModalProps> = ({
  platform, scopeType, scopeId, relinkUsername, onClose, onBound,
}) => {
  const { t } = useTranslation();
  const [taskId, setTaskId] = useState<string | null>(null);
  const [login, setLogin] = useState<SessionLoginState | null>(null);
  // Two shapes of start failure, because only one of them is worth retrying:
  // a 503 means the server has no browser service configured at all.
  const [startError, setStartError] =
    useState<'start_failed' | 'start_unavailable' | null>(null);
  // Set when /sms answers 409 — the task is already terminal server-side, so
  // Realtime will never deliver another status for it.
  const [sessionEnded, setSessionEnded] = useState(false);
  const [smsCode, setSmsCode] = useState('');
  const [smsBusy, setSmsBusy] = useState(false);
  const [smsError, setSmsError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const reported: ViewStatus = startError
    ?? (sessionEnded
      ? 'session_ended'
      : login?.status ?? (taskId ? 'connecting' : 'starting'));
  // The router seeds the row with a placeholder `waiting_scan` before the
  // browser has produced an image. Rendering that verbatim would tell the user
  // to scan an empty box, so it reads as "still fetching" until a QR arrives.
  const status: ViewStatus =
    reported === 'waiting_scan' && !login?.qrcode_data_url ? 'connecting' : reported;
  const terminal = TERMINAL.has(status);

  // Mirrors taskId/terminal for the unmount cleanup, which runs with [] deps
  // and so cannot close over live state.
  const liveRef = useRef<{ taskId: string | null; terminal: boolean }>({
    taskId: null, terminal: false,
  });
  useEffect(() => { liveRef.current = { taskId, terminal }; }, [taskId, terminal]);

  // Guards against a double DELETE: close() cancels, then the parent unmounts
  // us and the unmount net would fire for the same task id.
  const cancelledRef = useRef(false);

  // ── start / restart ──────────────────────────────────────────────────
  const begin = useCallback(async () => {
    cancelledRef.current = false;
    setStartError(null);
    setSessionEnded(false);
    setLogin(null);
    setSmsCode('');
    setSmsError(null);
    try {
      const { task_id } = await startSessionLogin({
        platform, scope_type: scopeType, scope_id: scopeId,
      });
      setTaskId(task_id);
    } catch (err) {
      console.error('distribution: start session login failed', err);
      setTaskId(null);
      // 503 = the endpoint's deliberate fail-fast on a missing
      // BROWSER_SERVICE_URL / token. It never becomes true by retrying, so it
      // gets its own state (and no retry button) rather than being lumped in
      // with transient network failures.
      setStartError(
        (err as { status?: number } | null)?.status === 503
          ? 'start_unavailable'
          : 'start_failed',
      );
    }
  }, [platform, scopeType, scopeId]);

  useEffect(() => { void begin(); }, [begin]);

  /** Abandon the in-flight login (if any) and start a fresh one. */
  const restart = useCallback(async () => {
    if (taskId && !terminal) {
      try {
        await cancelSessionLogin(taskId);
      } catch (err) {
        // Best-effort: the workflow times out on its own. Never block a retry.
        console.error('distribution: cancel session login before retry failed', err);
      }
    }
    setTaskId(null);
    await begin();
  }, [begin, taskId, terminal]);

  // ── Realtime: task_tracking.metadata.login ───────────────────────────
  useEffect(() => {
    if (!taskId) return;
    const supabase = getSupabaseClient();
    if (!supabase) {
      console.error('distribution: no Supabase client — session login cannot stream');
      return;
    }

    const apply = (row: unknown) => {
      const meta = (row as { metadata?: { login?: SessionLoginState } } | null)?.metadata;
      if (meta?.login) setLogin(meta.login);
    };

    // `task_id` from the REST call is the DBOS workflow id, which the router
    // deliberately reuses as `task_tracking.dbos_workflow_id` (it is NOT the
    // table's own uuid PK) — filtering on `id` would match nothing.
    const channel = supabase
      .channel(`session-login-${taskId}`)
      .on('postgres_changes', {
        event: 'UPDATE',
        schema: 'public',
        table: 'task_tracking',
        filter: `dbos_workflow_id=eq.${taskId}`,
      }, (payload) => apply(payload.new))
      .subscribe((chanStatus) => {
        if (chanStatus !== 'SUBSCRIBED') return;
        // Seed: the QR frame may already have been written before we joined.
        void supabase
          .from('task_tracking')
          .select('metadata')
          .eq('dbos_workflow_id', taskId)
          .maybeSingle()
          .then(({ data, error }) => {
            if (error) console.error('distribution: seed session login row failed', error);
            else apply(data);
          });
      });

    return () => { supabase.removeChannel(channel); };
  }, [taskId]);

  // ── success → tell the caller, then get out of the way ───────────────
  // Callers pass inline arrows, so onBound/onClose change identity on every
  // parent render. Keying the effect on them would re-fire onBound after the
  // reload it triggers — a loop. Fire once, off a latch.
  const cbRef = useRef({ onBound, onClose });
  cbRef.current = { onBound, onClose };
  const boundRef = useRef(false);
  useEffect(() => {
    if (status !== 'success' || boundRef.current) return;
    boundRef.current = true;
    cbRef.current.onBound();
    const timer = window.setTimeout(() => cbRef.current.onClose(), 1400);
    return () => window.clearTimeout(timer);
  }, [status]);

  // ── expiry countdown (display only) ──────────────────────────────────
  useEffect(() => {
    if (!login?.expires_at || terminal) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [login?.expires_at, terminal]);

  const secondsLeft = login?.expires_at
    ? Math.max(0, Math.round((new Date(login.expires_at).getTime() - now) / 1000))
    : null;

  // ── close (cancels a pending login) ──────────────────────────────────
  const cancelPending = useCallback((id: string | null, done: boolean) => {
    if (!id || done || cancelledRef.current) return;
    cancelledRef.current = true;
    cancelSessionLogin(id)
      .then((res) => {
        if (res && res.context_released === false) {
          console.warn('distribution: login cancelled but browser context outlives it', res.message);
        }
      })
      .catch((err) => {
        console.error('distribution: cancel session login failed', err);
      });
  }, []);

  const close = useCallback(() => {
    cancelPending(taskId, terminal);
    onClose();
  }, [cancelPending, onClose, taskId, terminal]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [close]);

  // Unmount safety net: a parent that drops the modal without calling our
  // close() (route change, error boundary) must not orphan the context.
  useEffect(() => () => {
    const { taskId: id, terminal: done } = liveRef.current;
    cancelPending(id, done);
  }, [cancelPending]);

  // The backend rejects anything but 4–8 digits with a FastAPI 422, whose body
  // is a pydantic error list rather than a SessionOpResult — so gate on the
  // same rule here instead of letting the user hit a shape we can't read.
  const smsValid = SMS_CODE_PATTERN.test(smsCode);

  const onSubmitSms = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!taskId || !smsValid) return;
    setSmsBusy(true);
    setSmsError(null);
    try {
      const res = await submitSmsCode(taskId, smsCode);
      // A 200 with success:false is the platform saying no — showing nothing
      // here would leave the user staring at an unchanged form.
      if (res && res.success === false) {
        // Three readings, checked most-specific first, and **none of them
        // echoes `res.message`**. That field is the same machine English as
        // `metadata.login.message` (see SERVER_DETAIL_COPY) — it used to be
        // rendered raw here, which put a sentence like "navigated to an
        // unexpected host (host=...)" under the user's code input.
        //   error_kind    — ours, not the account's: the browser service.
        //   code_rejected — the platform did not accept this code.
        //   otherwise     — translate by status; the raw string stays in
        //                   `res.detail`/the console for support.
        const statusCopy = SMS_SUBMIT_COPY[res.status as SessionLoginStatus] as Copy | undefined;
        setSmsError(
          res.detail?.error_kind
            ? t('distribution.session.smsInfraFailed', 'The browser service could not be reached — try again in a moment.')
            // `code_rejected` is the browser service's typed verdict on the
            // code we just sent: it went into the page, and the page is still
            // asking for one. Its own English message says so, but this is a
            // user-facing line under a possibly-Chinese UI, so it is
            // translated here rather than echoed (same rule as
            // SERVER_DETAIL_COPY above).
            : res.detail?.code_rejected
              ? t('distribution.session.smsRejected', 'That code was not accepted — the platform is still asking for one. Check it and send it again.')
              : statusCopy
                ? t(statusCopy[0], statusCopy[1])
                // Only an off-contract status reaches here (the map is total
                // over SessionLoginStatus). Say something true and generic
                // rather than nothing — silence is the bug this whole PR
                // series is about.
                : t('distribution.session.smsFailed', 'That code was rejected — check it and try again'),
        );
        if (!res.detail?.error_kind && !res.detail?.code_rejected) {
          // The untranslated original is worth keeping somewhere a bug report
          // can reach, just not on screen.
          console.error('distribution: sms submit refused', res.status, res.message);
        }
        // Deliberately NOT clearing `smsCode`. A rejected code is almost always
        // a mistyped digit or a stale one, and the next action is to correct
        // what is on screen — wiping it forces the user back to the SMS app to
        // re-read all six digits for the sake of one. The field stays enabled
        // and the Submit button re-enables (the value still passes `smsValid`),
        // so a retry is one edit away.
        return;
      }
      setSmsCode('');
    } catch (err) {
      console.error('distribution: submit sms code failed', err);
      // Three different failures, three different fixes. Collapsing them into
      // "that code was rejected" is worse than useless for the first one:
      //   409 — the login already ended (QR expired / workflow finished /
      //         cancelled) and the browser context is gone. No code will ever
      //         work now, so telling the user to re-check theirs traps them
      //         re-entering it forever. They need to start over.
      //   422 — malformed. `smsValid` should make this unreachable, but if the
      //         server's rule tightens, say what shape it wants rather than
      //         blaming the digits the user typed.
      //   else — the platform genuinely rejected the code.
      const status = (err as { status?: number } | null)?.status;
      // 404 joins 409: the backend returns it when the task row is gone, is
      // not a login task, or belongs to someone else. Whatever the cause, this
      // task id is dead for this user and the remedy is identical — start over.
      // (Only 409 is reachable from the normal flow; 404 needs a stale id.)
      if (status === 409 || status === 404) {
        // Flip the whole modal rather than just annotating the form: no
        // further Realtime update is coming for this task, and leaving the
        // code input on screen implies retrying it might work. The
        // session_ended view puts "Get a new code" in front of the user.
        setSessionEnded(true);
      } else if (status === 422) {
        setSmsError(t('distribution.session.smsInvalid', 'Enter the 4-8 digit code'));
      } else {
        setSmsError(t('distribution.session.smsFailed', 'That code was rejected — check it and try again'));
      }
    } finally {
      setSmsBusy(false);
    }
  };

  const platformLabel = PLATFORM_LABEL[platform] ?? platform;

  const STATUS_LABEL: Record<ViewStatus, string> = {
    starting: t('distribution.session.startingLabel', 'Preparing a browser session'),
    connecting: t('distribution.session.connectingLabel', 'Fetching the QR code'),
    start_failed: t('distribution.session.startFailedLabel', 'Could not start sign-in'),
    start_unavailable: t('distribution.session.startUnavailableLabel', 'QR sign-in is not set up on this server'),
    session_ended: t('distribution.session.sessionEndedLabel', 'This sign-in has ended'),
    waiting_scan: t('distribution.session.waitingScanLabel', 'Waiting for the scan'),
    scanned: t('distribution.session.scannedLabel', 'Scanned — confirm on your phone'),
    qrcode_expired: t('distribution.session.qrcodeExpiredLabel', 'QR code expired'),
    sms_required: t('distribution.session.smsRequiredLabel', 'SMS verification required'),
    success: t('distribution.session.successLabel', 'Account linked'),
    timeout: t('distribution.session.timeoutLabel', 'Sign-in timed out'),
    failed: t('distribution.session.failedLabel', 'Sign-in failed'),
    proxy_failed: t('distribution.session.proxyFailedLabel', 'Proxy unreachable'),
  };

  const STATUS_HINT: Record<ViewStatus, string> = {
    starting: t('distribution.session.startingHint', 'Opening an isolated browser for this account.'),
    connecting: t('distribution.session.connectingHint', 'The sign-in page is loading — the code appears in a moment.'),
    start_failed: t('distribution.session.startFailedHint', 'The request never reached the server. Check your connection and try again.'),
    start_unavailable: t('distribution.session.startUnavailableHint', 'The browser service this needs has not been configured. Retrying will not help — ask an administrator to set it up, or use Official Authorization instead.'),
    session_ended: t('distribution.session.sessionEndedHint', 'The code expired while this was open, so the verification code can no longer be used. Get a new code and scan again.'),
    waiting_scan: t('distribution.session.waitingScanHint', 'Open the app on your phone and scan the code to link this account.'),
    scanned: t('distribution.session.scannedHint', 'Tap Confirm in the app to finish signing in.'),
    qrcode_expired: t('distribution.session.qrcodeExpiredHint', 'Codes are short-lived. A fresh one is being fetched — or request it now.'),
    sms_required: t('distribution.session.smsRequiredHint', 'The platform sent a code to the phone number on this account. Enter it to continue.'),
    success: t('distribution.session.successHint', 'This account can now publish unattended.'),
    timeout: t('distribution.session.timeoutHint', 'Nobody scanned the code in time. Nothing was changed — start over when you are ready.'),
    failed: t('distribution.session.failedHint', 'The platform refused the sign-in. Try again, and check whether the account is restricted.'),
    proxy_failed: t('distribution.session.proxyFailedHint', 'The egress proxy for this account could not be reached — the account itself is fine. Fix the proxy, then retry.'),
  };

  // `failed` and `timeout` each cover two very different things, and the
  // generic copy blames the wrong party for one of them every time:
  //   - "the platform refused the sign-in" for a `ConnectError` sends the user
  //     off checking whether their account is banned. On 2026-08-09 a routine
  //     redeploy restarted nous-browser mid-scan and produced exactly that;
  //     the same account signed in fine six minutes later.
  //   - "nobody scanned the code in time" for a transport timeout blames the
  //     user for not scanning a code that our own service never answered for.
  // `detail.error_kind` is the backend's own marker for "this is ours, not the
  // account's" (spec 7.8, `SessionErrorKind`) — when it is present, say so.
  // `proxy_failed` is excluded on purpose: it is already its own status with
  // its own correct copy, and it points at a *different* fix (the account's
  // egress proxy, not our browser service).
  //
  // A third reading hides in the same shape: `detail.reason` ===
  // `identity_unresolved` means the *opposite* of a refusal — the platform let
  // us in and we could not tell which account it was. Blaming either party is
  // wrong there, so it gets its own copy rather than sharing the platform one.
  //
  // The three readings are mutually exclusive by construction: `infraKind`
  // wins when both markers are somehow present, because "we never reached a
  // conclusion" subsumes any `reason` riding along with it.
  const failureDetail = (status === 'failed' || status === 'timeout')
    ? login?.detail
    : undefined;
  const infraKind = failureDetail?.error_kind;
  // Gated on `failed` alone: this is a terminal conclusion from the state
  // fetch, never a timeout, and never `proxy_failed` (which keeps its own copy
  // pointing at the account's egress proxy).
  const identityUnresolved = !infraKind
    && status === 'failed'
    && failureDetail?.reason === IDENTITY_UNRESOLVED;

  let label = STATUS_LABEL[status];
  let hint = STATUS_HINT[status];
  if (infraKind) {
    label = t('distribution.session.infraLabel', 'Service temporarily unavailable');
    hint = t(
      'distribution.session.failedInfraHint',
      'This failed on our side before reaching the platform — the account is fine. Retry in a moment; if it persists the browser service needs looking at.',
    );
  } else if (identityUnresolved) {
    label = t(
      'distribution.session.identityUnresolvedLabel',
      'Signed in, but the account is unidentified',
    );
    hint = t(
      'distribution.session.identityUnresolvedHint',
      'The sign-in itself succeeded — we could not read which account it was, so nothing was linked and the account is untouched. Try binding again; if it keeps happening, contact support.',
    );
  }

  // The detail line under the hint. Always our own translation, never
  // `login.message` — with the map now total over the server statuses, the
  // remaining `undefined` cases are the four local-only phases (starting /
  // connecting / start_failed / start_unavailable) and `session_ended`, none of
  // which describe a page the browser service looked at. `session_ended`
  // matters most here: it can still be holding the *previous* state's message,
  // so echoing would print a stale English line under "This sign-in has ended".
  const serverDetailCopy = SERVER_DETAIL_COPY[status as SessionLoginStatus] as Copy | undefined;
  const detailLine = serverDetailCopy ? t(serverDetailCopy[0], serverDetailCopy[1]) : undefined;

  // Transient-and-ours reads as `warn`, not `danger`: nothing is broken about
  // the user's account and the remedy is simply to retry (bind again, or wait
  // a moment) — true of the identity case for exactly the same reason.
  const tone = infraKind || identityUnresolved ? 'warn' : TONE[status];
  const busy = status === 'starting' || status === 'connecting';
  const showQr = Boolean(login?.qrcode_data_url) && (status === 'waiting_scan' || status === 'scanned');
  const canRetry = RETRYABLE.has(status);
  // "Cancel" only when there is a live login to abandon; otherwise "Close".
  const canCancel = Boolean(taskId) && !terminal;

  return (
    <div className="picker-overlay" role="presentation" onClick={close}>
      <div
        className="picker sess"
        role="dialog"
        aria-modal="true"
        aria-label={t('distribution.session.title', 'Sign in with QR code')}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="picker-head">
          <div>
            <h3>{t('distribution.session.title', 'Sign in with QR code')}</h3>
            <p>
              {relinkUsername
                ? t('distribution.session.relinkSubtitle', 'Re-link {{name}} — the browser session expired.', { name: relinkUsername })
                : t('distribution.session.subtitle', 'Scan with the {{platform}} app to link the account.', { platform: platformLabel })}
            </p>
          </div>
          <button
            type="button"
            className="picker-close"
            aria-label={t('distribution.session.close', 'Close')}
            onClick={close}
          >
            <X size={16} />
          </button>
        </div>

        <div className="sess-body">
          <div className={`sess-qr tone-${tone}`}>
            {showQr ? (
              <img src={login!.qrcode_data_url!} alt={t('distribution.session.qrAlt', 'Sign-in QR code')} />
            ) : (
              <div className="sess-qr-ph">
                {status === 'success' && <CheckCircle2 size={30} />}
                {status === 'scanned' && <Smartphone size={30} />}
                {(status === 'proxy_failed' || status === 'start_unavailable') && <ShieldAlert size={30} />}
                {(status === 'failed' || status === 'timeout' || status === 'start_failed') && <AlertTriangle size={30} />}
                {busy && <Loader2 size={30} className="spin" />}
                {(status === 'qrcode_expired' || status === 'session_ended') && <QrCode size={30} />}
                {status === 'sms_required' && <Smartphone size={30} />}
              </div>
            )}
          </div>

          <div className="sess-info">
            <div className={`sess-status tone-${tone}`}>
              <span className="d" />
              {label}
            </div>
            <p className="sess-hint">{hint}</p>
            {/* Server-authored detail sits under the generic hint — it is the
                only place a state-specific reason surfaces. Where the status
                already tells us what that reason is, we say it in the user's
                language instead of echoing our own English (SERVER_DETAIL_COPY). */}
            {detailLine && <p className="sess-detail">{detailLine}</p>}
            {secondsLeft !== null && status === 'waiting_scan' && (
              <p className="sess-expiry">
                {t('distribution.session.expiresIn', 'Expires in {{n}}s', { n: secondsLeft })}
              </p>
            )}

            {status === 'sms_required' && (
              <form className="sess-sms" onSubmit={onSubmitSms}>
                <label htmlFor="sess-sms-code">
                  {t('distribution.session.smsLabel', 'Verification code')}
                </label>
                <div className="sess-sms-row">
                  <input
                    id="sess-sms-code"
                    className="input"
                    value={smsCode}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={8}
                    aria-invalid={smsCode.length > 0 && !smsValid}
                    placeholder={t('distribution.session.smsPlaceholder', '4-8 digit code')}
                    // Strip anything the server would 422 on as it is typed —
                    // paste included.
                    onChange={(e) => setSmsCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                    disabled={smsBusy}
                  />
                  <button
                    type="submit"
                    className="btn btn-tint-indigo btn-sm"
                    disabled={smsBusy || !smsValid}
                  >
                    {smsBusy
                      ? t('distribution.session.smsSubmitting', 'Sending...')
                      : t('distribution.session.smsSubmit', 'Submit')}
                  </button>
                </div>
                {smsCode.length > 0 && !smsValid && (
                  <p className="sess-detail tone-warn">
                    {t('distribution.session.smsInvalid', 'Enter the 4-8 digit code')}
                  </p>
                )}
                {smsError && <p className="sess-detail tone-danger">{smsError}</p>}
              </form>
            )}
          </div>
        </div>

        <div className="picker-foot">
          <button type="button" className="btn btn-ghost btn-sm" onClick={close}>
            {canCancel
              ? t('common.cancel', 'Cancel')
              : t('common.close', 'Close')}
          </button>
          {canRetry && (
            <button type="button" className="btn btn-tint-indigo btn-sm" onClick={() => void restart()}>
              <RefreshCw size={13} /> {t('distribution.session.retry', 'Get a new code')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default SessionLoginModal;
