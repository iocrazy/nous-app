import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import type { SessionLoginState } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const startSessionLogin = vi.fn();
const submitSmsCode = vi.fn();
const cancelSessionLogin = vi.fn();
// The modal asks which sign-in this platform uses before it draws one. Mocked
// rather than left to the real fetch so this file keeps testing the QR shape on
// purpose instead of by accident — the SMS shape has its own file.
const getPlatformCapabilities = vi.fn();

// Spread the real module so SMS_CODE_PATTERN stays the one the service and
// the backend agree on — a hand-copied literal here would let the submit gate
// drift away from what the endpoint accepts without any test noticing.
vi.mock('../../services/distributionService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/distributionService')>()),
  startSessionLogin: (...a: unknown[]) => startSessionLogin(...a),
  submitSmsCode: (...a: unknown[]) => submitSmsCode(...a),
  cancelSessionLogin: (...a: unknown[]) => cancelSessionLogin(...a),
  getPlatformCapabilities: () => getPlatformCapabilities(),
}));

// Minimal Realtime double: captures the postgres_changes handler so a test can
// hand the component a `task_tracking` row exactly the way Supabase would.
let updateHandler: ((payload: { new: unknown }) => void) | null = null;
let subscribeCb: ((status: string) => void) | null = null;
const removeChannel = vi.fn();
const seedRow = { data: null as unknown, error: null as unknown };

vi.mock('../../supabaseClient', () => {
  const channelObj = {
    on: (_evt: string, _cfg: unknown, cb: (p: { new: unknown }) => void) => {
      updateHandler = cb;
      return channelObj;
    },
    subscribe: (cb: (s: string) => void) => {
      subscribeCb = cb;
      return channelObj;
    },
  };
  return {
    getSupabaseClient: () => ({
      channel: () => channelObj,
      removeChannel,
      from: () => ({
        select: () => ({
          eq: () => ({ maybeSingle: () => Promise.resolve(seedRow) }),
        }),
      }),
    }),
  };
});

import SessionLoginModal from './SessionLoginModal';

const onClose = vi.fn();
const onBound = vi.fn();

const mount = () =>
  render(
    <SessionLoginModal
      platform="douyin"
      scopeType="user"
      scopeId="self"
      onClose={onClose}
      onBound={onBound}
    />,
  );

/** Deliver a `metadata.login` patch the way the workflow writes it. */
const pushLogin = async (login: Partial<SessionLoginState>) => {
  await act(async () => {
    updateHandler?.({
      new: { metadata: { login: { platform: 'douyin', ...login } } },
    });
  });
};

const QR = 'data:image/png;base64,iVBORw0KGgo=';

describe('SessionLoginModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateHandler = null;
    subscribeCb = null;
    seedRow.data = null;
    startSessionLogin.mockResolvedValue({ task_id: 'task-1' });
    submitSmsCode.mockResolvedValue({ success: true, status: 'success', message: 'ok', detail: {} });
    cancelSessionLogin.mockResolvedValue({ cancelled: true, context_released: true, message: 'ok' });
    getPlatformCapabilities.mockResolvedValue({
      douyin: { platform: 'douyin', login_method: 'qrcode' },
    });
  });

  it('starts a login and subscribes to the task row', async () => {
    mount();
    await waitFor(() => expect(startSessionLogin).toHaveBeenCalledWith({
      platform: 'douyin', scope_type: 'user', scope_id: 'self',
    }));
    // Until the first metadata.login write lands there is still a live,
    // non-silent state — never a blank modal.
    expect(screen.getByText(/Fetching the QR code/i)).toBeInTheDocument();
    await waitFor(() => expect(updateHandler).toBeTruthy());
  });

  it('seeds from the row on SUBSCRIBED, so a QR written before the join is not lost', async () => {
    seedRow.data = { metadata: { login: { platform: 'douyin', status: 'waiting_scan', qrcode_data_url: QR } } };
    mount();
    await waitFor(() => expect(subscribeCb).toBeTruthy());
    await act(async () => { subscribeCb?.('SUBSCRIBED'); });
    await waitFor(() =>
      expect(screen.getByAltText(/Sign-in QR code/i)).toBeInTheDocument());
  });

  it('does not tell the user to scan a code that has not arrived', async () => {
    // The router seeds metadata.login with waiting_scan + a null image at
    // create time; rendering that verbatim would show an empty white box
    // under "Waiting for the scan".
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'waiting_scan', qrcode_data_url: null });
    expect(screen.getByText(/Fetching the QR code/i)).toBeInTheDocument();
    expect(screen.queryByAltText(/Sign-in QR code/i)).toBeNull();
  });

  it('renders the QR code and countdown while waiting for a scan', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'waiting_scan',
      qrcode_data_url: QR,
      expires_at: new Date(Date.now() + 120_000).toISOString(),
    });
    expect(screen.getByAltText(/Sign-in QR code/i)).toHaveAttribute('src', QR);
    expect(screen.getByText(/Waiting for the scan/i)).toBeInTheDocument();
    expect(screen.getByText(/Expires in/i)).toBeInTheDocument();
  });

  it('shows the scanned hand-off state', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'scanned', qrcode_data_url: QR });
    expect(screen.getByText(/confirm on your phone/i)).toBeInTheDocument();
  });

  it('offers a manual refresh when the code expires', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'qrcode_expired' });
    expect(screen.getByText('QR code expired')).toBeInTheDocument();
    const retry = screen.getByRole('button', { name: /Get a new code/i });
    await act(async () => { fireEvent.click(retry); });
    // Retry abandons the stale context before asking for another one.
    expect(cancelSessionLogin).toHaveBeenCalledWith('task-1');
    await waitFor(() => expect(startSessionLogin).toHaveBeenCalledTimes(2));
  });

  it('collects and submits the SMS challenge', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required', message: 'Code sent to 138****0000' });
    expect(screen.getByText(/SMS verification required/i)).toBeInTheDocument();
    // The detail line is present and translated, not the server's own string.
    // `metadata.login.message` on this status is never platform copy — it is
    // `LoginJudgement.reason`, machine-authored English from our own browser
    // service — so this fixture's "Code sent to ..." shape does not occur in
    // the wild. See the sibling test below for the string that actually ships.
    expect(screen.getByText('The platform is asking for a verification code.'))
      .toBeInTheDocument();
    expect(screen.queryByText('Code sent to 138****0000')).toBeNull();

    const input = screen.getByLabelText(/Verification code/i);
    fireEvent.change(input, { target: { value: '123456' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });
    expect(submitSmsCode).toHaveBeenCalledWith('task-1', '123456');
  });

  // D4. The exact string a user photographed on the SMS screen, sitting in the
  // middle of an otherwise Chinese UI. Its source is ours, not the platform's:
  // `browser/app/platforms/douyin.py` → `LoginJudgement.reason` →
  // `StatusSnapshot.message` → backend `login_metadata` → `metadata.login.message`.
  it('does not print the browser service\'s English at the user', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'sms_required',
      message: 'the platform is asking for a verification code',
    });

    expect(screen.queryByText('the platform is asking for a verification code')).toBeNull();
    // Replaced by copy that goes through i18n, so a zh user reads Chinese.
    expect(screen.getByText('The platform is asking for a verification code.'))
      .toBeInTheDocument();
  });

  // D5. The map is now total over the server statuses, so "a state we have no
  // copy for" no longer exists. Every one of them is asserted here rather than
  // spot-checked: a `Partial` map would compile, and the failure mode of a
  // missed status is a bare English (or half-Chinese) sentence that no test
  // notices.
  const SERVER_LINES: [SessionLoginState['status'], string, string][] = [
    ['waiting_scan', 'QR code displayed', 'The sign-in page is showing a QR code.'],
    ['scanned', 'scanned; waiting for confirmation on the phone: 扫码成功',
      'The platform registered the scan and is waiting for you to approve it.'],
    ['qrcode_expired', 'QR code expired: 二维码已失效',
      'The platform marked this code as expired, so a fresh one was requested.'],
    ['identity_challenge', 'the platform is asking to verify your identity: 接收短信验证码',
      'The platform is showing its identity-verification step — no code has been sent yet.'],
    ['sms_required', 'the platform is asking for a verification code',
      'The platform is asking for a verification code.'],
    ['success', 'reached the creator console with no login prompt',
      "The platform's creator console loaded — the sign-in is complete."],
    ['timeout', 'QR login timed out', 'The browser session for this sign-in has ended.'],
    ['failed', 'navigated to an unexpected host (host=example.com)',
      'The browser session ended without a completed sign-in.'],
    ['proxy_failed', 'net::ERR_PROXY_CONNECTION_FAILED',
      "The browser could not reach the platform through this account's proxy."],
  ];

  it.each(SERVER_LINES)(
    'translates the %s detail line instead of echoing the service',
    async (status, serverMessage, translated) => {
      mount();
      await waitFor(() => expect(updateHandler).toBeTruthy());
      // waiting_scan needs an image or the modal reads it as "still fetching".
      await pushLogin({ status, message: serverMessage, qrcode_data_url: QR });

      expect(screen.queryByText(serverMessage)).toBeNull();
      expect(screen.getByText(translated)).toBeInTheDocument();
    },
  );

  it('drops the Chinese tail rather than pasting it after English', async () => {
    // `扫码成功` is not the platform talking — it is whichever entry of our own
    // SCANNED_MARKERS tuple matched, i.e. the status restated in Chinese. The
    // rejected alternative was "i18n prefix + raw tail", which reads as a debug
    // string in both locales. Nothing is lost: the full original still rides in
    // `detail.reason`.
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'scanned',
      message: 'scanned; waiting for confirmation on the phone: 扫码成功',
      detail: { reason: 'scanned; waiting for confirmation on the phone: 扫码成功' },
    });

    expect(document.body.textContent).not.toContain('扫码成功');
  });

  it('never prints a stale server line once the session has ended', async () => {
    // `session_ended` is set locally on a 409 and keeps whatever `login` was
    // last delivered — echoing its message would put the *previous* state's
    // English under "This sign-in has ended".
    submitSmsCode.mockRejectedValueOnce(
      Object.assign(new Error('distribution api failed: 409'), { status: 409 }),
    );
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'sms_required',
      message: 'the platform is asking for a verification code',
    });
    fireEvent.change(screen.getByLabelText(/Verification code/i), { target: { value: '123456' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });

    expect(await screen.findByText(/This sign-in has ended/i)).toBeInTheDocument();
    expect(screen.queryByText('the platform is asking for a verification code')).toBeNull();
    expect(screen.queryByText('The platform is asking for a verification code.')).toBeNull();
  });

  it('will not send a code the server would 422 on', async () => {
    // The endpoint requires ^\d{4,8}$. A shorter or non-numeric value comes
    // back as a FastAPI pydantic body, which has no `success`/`message` — so
    // the form must never let it out.
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });
    const input = screen.getByLabelText(/Verification code/i) as HTMLInputElement;
    const submit = screen.getByRole('button', { name: /^Submit$/i });

    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: '12a3' } });
    expect(input.value).toBe('123');            // letters stripped as typed
    expect(submit).toBeDisabled();              // 3 digits is still too short
    expect(screen.getByText(/Enter the 4-8 digit code/i)).toBeInTheDocument();

    fireEvent.change(input, { target: { value: '1234567890' } });
    expect(input.value).toBe('12345678');       // capped at the 8-digit max
    expect(submit).toBeEnabled();

    await act(async () => { fireEvent.click(submit); });
    expect(submitSmsCode).toHaveBeenCalledWith('task-1', '12345678');
  });

  it('turns a 409 into "start over", not "check your code"', async () => {
    // The real path: the user sits on the code form past the 5-minute TTL.
    // The browser context is gone and no code will ever work, so telling them
    // to re-check their digits would trap them re-entering it forever.
    submitSmsCode.mockRejectedValueOnce(
      Object.assign(new Error('distribution api failed: 409'), { status: 409 }),
    );
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });
    fireEvent.change(screen.getByLabelText(/Verification code/i), { target: { value: '123456' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });

    expect(await screen.findByText(/This sign-in has ended/i)).toBeInTheDocument();
    expect(screen.queryByText(/That code was rejected/i)).toBeNull();
    // The code form is gone and the only useful action is offered instead.
    expect(screen.queryByLabelText(/Verification code/i)).toBeNull();
    expect(screen.getByRole('button', { name: /Get a new code/i })).toBeInTheDocument();
    // Terminal server-side — a DELETE here would 404.
    const closers = screen.getAllByRole('button', { name: /^Close$/i });
    await act(async () => { fireEvent.click(closers[closers.length - 1]); });
    expect(cancelSessionLogin).not.toHaveBeenCalled();
  });

  it('treats a 404 the same as a 409 — the id is dead either way', async () => {
    // The backend answers 404 when the task row is gone, is not a login task,
    // or belongs to another user. All three mean this id is unusable for this
    // user, and the remedy is the same one 409 gets: start over. Showing
    // "check your code" would be just as much of a dead end.
    submitSmsCode.mockRejectedValueOnce(
      Object.assign(new Error('distribution api failed: 404'), { status: 404 }),
    );
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });
    fireEvent.change(screen.getByLabelText(/Verification code/i), { target: { value: '123456' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });

    expect(await screen.findByText(/This sign-in has ended/i)).toBeInTheDocument();
    expect(screen.queryByText(/That code was rejected/i)).toBeNull();
    expect(screen.queryByLabelText(/Verification code/i)).toBeNull();
  });

  it('names a 422 as a format problem, not a wrong code', async () => {
    const err = Object.assign(new Error('distribution api failed: 422'), { status: 422 });
    submitSmsCode.mockRejectedValueOnce(err);
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });
    fireEvent.change(screen.getByLabelText(/Verification code/i), { target: { value: '123456' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });
    expect(await screen.findByText(/Enter the 4-8 digit code/i)).toBeInTheDocument();
    expect(screen.queryByText(/That code was rejected/i)).toBeNull();
  });

  it('surfaces a 200-with-success:false SMS rejection', async () => {
    // The endpoint answers 200 with a typed envelope — branching on the HTTP
    // status alone would swallow the platform's "no". D5: the line the user
    // reads is now translated by status, not `res.message` echoed raw.
    submitSmsCode.mockResolvedValueOnce({
      success: false, status: 'sms_required', message: 'Wrong verification code', detail: {},
    });
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });
    fireEvent.change(screen.getByLabelText(/Verification code/i), { target: { value: '111111' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });
    expect(await screen.findByText('The platform is still asking for a verification code.'))
      .toBeInTheDocument();
    expect(screen.queryByText('Wrong verification code')).toBeNull();
  });

  // D5-B. Every `success: false` status the /sms endpoint can answer with,
  // asserted to produce translated copy — the branch that used to render
  // `res.message` verbatim, which is where a line like "navigated to an
  // unexpected host (host=...)" would land under the user's code input.
  const SMS_SUBMIT_LINES: [string, string, string][] = [
    ['qrcode_expired', 'the QR code expired: 二维码已失效',
      'The QR code expired before the code was accepted'],
    ['timeout', 'another operation on this login session is still running',
      'This sign-in ended before the code could be used'],
    ['proxy_failed', 'net::ERR_PROXY_CONNECTION_FAILED at http://p:1',
      "this account's proxy is unreachable"],
    ['failed', 'navigated to an unexpected host (host=example.com)',
      'That code was rejected'],
    ['waiting_scan', 'no verification code input is present: QR code displayed',
      'The platform is no longer asking for a code'],
  ];

  it.each(SMS_SUBMIT_LINES)(
    'translates a %s answer to /sms instead of echoing it',
    async (status, serverMessage, expected) => {
      submitSmsCode.mockResolvedValueOnce({
        success: false, status, message: serverMessage, detail: {},
      });
      mount();
      await waitFor(() => expect(updateHandler).toBeTruthy());
      await pushLogin({ status: 'sms_required' });
      fireEvent.change(screen.getByLabelText(/Verification code/i), { target: { value: '333333' } });
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
      });

      expect(await screen.findByText(new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i')))
        .toBeInTheDocument();
      expect(screen.queryByText(serverMessage)).toBeNull();
    },
  );

  it('tells the user a rejected code was rejected, and keeps what they typed', async () => {
    // **The user-visible half of the "wrong code, zero feedback" tombstone.**
    //
    // The shape below is what the chain now produces for a code the platform
    // did not accept: still `sms_required` (the page is genuinely still asking),
    // but `success: false` + `detail.code_rejected`. Before the fix it arrived
    // as `success: true` and this component took the success path — clearing
    // the field and saying nothing at all.
    submitSmsCode.mockResolvedValueOnce({
      success: false,
      status: 'sms_required',
      message: 'the verification code was not accepted; the page is still asking for one',
      detail: { code_rejected: true, submitted: true },
    });
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });
    const input = screen.getByLabelText(/Verification code/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '111111' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });

    expect(await screen.findByText(/That code was not accepted/i)).toBeInTheDocument();
    // The machine-authored English never reaches the user.
    expect(screen.queryByText(/still asking for one\.$/)).toBeNull();
    // Retry is possible without re-reading the SMS: the digits are still there
    // and the button is live again.
    expect(input.value).toBe('111111');
    expect(screen.getByRole('button', { name: /^Submit$/i })).toBeEnabled();
    // The form stays — this is not a terminal state.
    expect(screen.queryByText(/This sign-in has ended/i)).toBeNull();
  });

  it('does not call a rejected code a success — the field must not silently clear', async () => {
    // Same event, asserted from the other side: whatever copy we choose, the
    // one outcome that is never acceptable is the pre-fix one — input wiped,
    // no error anywhere on screen.
    submitSmsCode.mockResolvedValueOnce({
      success: false,
      status: 'sms_required',
      message: 'not accepted',
      detail: { code_rejected: true },
    });
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });
    const input = screen.getByLabelText(/Verification code/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '222222' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });

    expect(input.value).not.toBe('');
    expect(screen.getByText(/not accepted/i)).toBeInTheDocument();
  });

  it('names an infrastructure failure as such, not as a bad code', async () => {
    submitSmsCode.mockResolvedValueOnce({
      success: false, status: 'failed', message: 'boom',
      detail: { error_kind: 'browser_unreachable' },
    });
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });
    fireEvent.change(screen.getByLabelText(/Verification code/i), { target: { value: '222222' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });
    expect(await screen.findByText(/browser service could not be reached/i)).toBeInTheDocument();
  });

  it('surfaces a transport-level SMS failure instead of failing silently', async () => {
    submitSmsCode.mockRejectedValueOnce(new Error('400'));
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });
    fireEvent.change(screen.getByLabelText(/Verification code/i), { target: { value: '000000' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });
    expect(await screen.findByText(/That code was rejected/i)).toBeInTheDocument();
  });

  it('distinguishes a dead proxy from a rejected account', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'proxy_failed' });
    expect(screen.getByText('Proxy unreachable')).toBeInTheDocument();
    // The whole point of the separate status: say it is not the account.
    expect(screen.getByText(/the account itself is fine/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Get a new code/i })).toBeInTheDocument();
  });

  it('renders each remaining terminal state with an actionable message', async () => {
    const cases: Array<[SessionLoginState['status'], RegExp]> = [
      ['timeout', /Sign-in timed out/i],
      ['failed', /Sign-in failed/i],
    ];
    for (const [status, label] of cases) {
      // Drop the previous iteration's handler, or waitFor would pass on the
      // stale one and push into an unmounted tree.
      updateHandler = null;
      const view = mount();
      await waitFor(() => expect(updateHandler).toBeTruthy());
      await pushLogin({ status });
      expect(screen.getByText(label)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Get a new code/i })).toBeInTheDocument();
      view.unmount();
    }
  });

  // P3-1. A `failed` from the workflow means one of two opposite things, and
  // the pair below is the whole point: the user is either sent to check their
  // account for a ban, or told to wait — never both, and never the wrong one.
  // The signal is `metadata.login.detail.error_kind` (backend
  // `SessionErrorKind`), which only exists once the workflow propagates it.
  it('blames the platform only when the platform actually said no', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'failed', message: 'account is restricted' });

    expect(screen.getByText('Sign-in failed')).toBeInTheDocument();
    expect(screen.getByText(/The platform refused the sign-in/i)).toBeInTheDocument();
    expect(screen.queryByText(/Service temporarily unavailable/i)).toBeNull();
  });

  it('names our own outage as ours instead of blaming the account', async () => {
    // The 2026-08-09 case verbatim: nous-browser was restarted by a deploy
    // 30s before the scan. `ConnectError` → error_kind `unreachable`.
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'failed',
      message: 'browser service unreachable (ConnectError)',
      detail: { error_kind: 'unreachable' },
    });

    expect(screen.getByText('Service temporarily unavailable')).toBeInTheDocument();
    expect(screen.getByText(/failed on our side before reaching the platform/i))
      .toBeInTheDocument();
    // The sentence that sent the user hunting for a ban must be gone.
    expect(screen.queryByText(/The platform refused the sign-in/i)).toBeNull();
    expect(screen.queryByText(/check whether the account is restricted/i)).toBeNull();
    // Still retryable — waiting a moment is exactly the remedy.
    expect(screen.getByRole('button', { name: /Get a new code/i })).toBeInTheDocument();
  });

  it('does not blame the user for a timeout that was our transport failing', async () => {
    // A read timeout against our own browser service also arrives as a
    // terminal `timeout`, where the stock copy says nobody scanned in time —
    // blaming the user for not scanning a code we never managed to ask about.
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'timeout',
      message: 'browser service timed out after 20.0s',
      detail: { error_kind: 'timeout' },
    });

    expect(screen.getByText('Service temporarily unavailable')).toBeInTheDocument();
    expect(screen.queryByText(/Nobody scanned the code in time/i)).toBeNull();
  });

  // D2. A third reading of the same `status: 'failed'` shape: the platform let
  // us in and we could not tell *who* signed in (browser `IdentityUnresolved`
  // → typed 409 → `detail.reason`). It carries no `error_kind`, so it is
  // byte-for-byte a platform refusal to any check that only looks at that key.
  it('says the sign-in worked when only the identity could not be read', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'failed',
      message: "cannot identify the douyin account: cookie 'uid_tt' was not present after login",
      detail: { reason: 'identity_unresolved' },
    });

    expect(screen.getByText('Signed in, but the account is unidentified')).toBeInTheDocument();
    expect(screen.getByText(/could not read which account it was/i)).toBeInTheDocument();
    // Neither of the other two tiers may leak through: one would send the user
    // hunting for a ban that never happened, the other would blame our
    // browser service for something it answered correctly.
    expect(screen.queryByText(/The platform refused the sign-in/i)).toBeNull();
    expect(screen.queryByText(/check whether the account is restricted/i)).toBeNull();
    expect(screen.queryByText(/Service temporarily unavailable/i)).toBeNull();
    // Binding again is the remedy, so the retry has to be on screen.
    expect(screen.getByRole('button', { name: /Get a new code/i })).toBeInTheDocument();
  });

  // --- the identity challenge (2026-08-11) ---------------------------------
  //
  // A Douyin bind stopped on the platform's own 身份验证 screen: two cards
  // (接收短信验证码 / 发送短信验证) and nothing sent until one is clicked. The
  // flow clicked neither, the judge read the code field that screen renders as
  // `sms_required`, and this modal said "The platform sent a code to the phone
  // number on this account" — so the user waited out the TTL for a message
  // that had never been requested.

  it('does not claim a code was sent while the platform is still asking how to verify', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'identity_challenge',
      message: 'the platform is asking to verify your identity: 接收短信验证码, 发送短信验证',
    });

    expect(screen.getByText(/Identity verification$/)).toBeInTheDocument();
    expect(screen.getByText(/nothing has been sent to your phone yet/i)).toBeInTheDocument();
    // The sentence that caused the wait, in any of its forms.
    expect(screen.queryByText(/sent a code to the phone number/i)).toBeNull();
    expect(screen.queryByText(/We asked the platform to text a code/i)).toBeNull();
    // And no code box: there is nothing to type yet.
    expect(screen.queryByLabelText(/Verification code/i)).toBeNull();
  });

  it('stays neutral about a code prompt nobody asked for on the user\'s behalf', async () => {
    // `sms_required` with no `code_requested`: a code field is on screen and
    // we have no evidence anything was sent. Claiming otherwise is the bug;
    // saying nothing at all would be the other one.
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required' });

    expect(screen.getByText(/asking for a verification code for this account/i))
      .toBeInTheDocument();
    expect(screen.queryByText(/sent a code to the phone number/i)).toBeNull();
    expect(screen.queryByText(/We asked the platform to text a code/i)).toBeNull();
    // The form is still there — the user may well have a code in hand.
    expect(screen.getByLabelText(/Verification code/i)).toBeInTheDocument();
  });

  it('says a code was requested only when the service actually asked for one', async () => {
    // `code_requested` is set by the browser service latching its own landed
    // click (the 接收短信验证码 card, or the 获取验证码 button behind it) —
    // never by observing that a code field exists.
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'sms_required', detail: { code_requested: true } });

    expect(screen.getByText(/We asked the platform to text a code/i)).toBeInTheDocument();
    expect(screen.queryByText(/asking for a verification code for this account/i)).toBeNull();
  });

  it('explains a verification screen it could not get through, and never blames the account', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'failed',
      message: 'the platform is asking to verify your identity and the SMS option could not be selected',
      detail: { reason: 'identity_challenge_unclickable' },
    });

    expect(screen.getByText('Identity verification could not be completed')).toBeInTheDocument();
    expect(screen.getByText(/did not offer an option we can complete automatically/i))
      .toBeInTheDocument();
    // The generic `failed` copy sends the user hunting for a ban that never
    // happened; the platform refused nothing here.
    expect(screen.queryByText(/The platform refused the sign-in/i)).toBeNull();
    // No code was sent, so nothing may imply one is on its way.
    expect(screen.queryByText(/sent a code to the phone number/i)).toBeNull();
    expect(screen.getByRole('button', { name: /Get a new code/i })).toBeInTheDocument();
  });

  it('tells a slider challenge apart from a stall, and does not say "scan again"', async () => {
    // Same label, deliberately different remedy. Rescanning a platform that has
    // escalated to a slider produces the same slider, so the copy that helps
    // everywhere else ("get a new code and scan again") is the one sentence
    // that must not appear here.
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({
      status: 'failed',
      message: 'the platform escalated to a challenge this service cannot complete (滑块)',
      detail: { reason: 'identity_challenge_blocked', blocking_marker: '滑块' },
    });

    expect(screen.getByText('Identity verification could not be completed')).toBeInTheDocument();
    expect(screen.getByText(/slider or picture check/i)).toBeInTheDocument();
    expect(screen.queryByText(/Get a new code and scan again/i)).toBeNull();
    expect(screen.queryByText(/sent a code to the phone number/i)).toBeNull();
  });

  it('lands each failure payload on exactly one of the three tiers', async () => {
    // The whole point of the trio: three payloads that differ only inside
    // `detail` must produce three different sentences, and never two at once.
    const LABELS = {
      platform: /^Sign-in failed$/,
      infra: /^Service temporarily unavailable$/,
      identity: /^Signed in, but the account is unidentified$/,
      challenge: /^Identity verification could not be completed$/,
    } as const;
    const cases: Array<[keyof typeof LABELS, Partial<SessionLoginState>]> = [
      ['platform', { status: 'failed', message: 'account is restricted' }],
      ['infra', { status: 'failed', detail: { error_kind: 'unreachable' } }],
      ['identity', { status: 'failed', detail: { reason: 'identity_unresolved' } }],
      ['challenge', { status: 'failed', detail: { reason: 'identity_challenge_unclickable' } }],
      ['challenge', { status: 'failed', detail: { reason: 'identity_challenge_stalled' } }],
      ['challenge', { status: 'failed', detail: { reason: 'identity_challenge_blocked' } }],
      // Both markers at once: "we never reached a conclusion" subsumes any
      // reason riding along with it, so infra wins rather than both showing.
      ['infra', {
        status: 'failed',
        detail: { error_kind: 'unreachable', reason: 'identity_unresolved' },
      }],
    ];
    for (const [tier, payload] of cases) {
      updateHandler = null;
      const view = mount();
      await waitFor(() => expect(updateHandler).toBeTruthy());
      await pushLogin(payload);

      for (const [name, label] of Object.entries(LABELS)) {
        if (name === tier) expect(screen.getByText(label)).toBeInTheDocument();
        else expect(screen.queryByText(label)).toBeNull();
      }
      view.unmount();
    }
  });

  it('keeps a dead proxy pointing at the proxy, not at our browser service', async () => {
    // `proxy_failed` is deliberately outside the infra branch: it is the
    // account's egress proxy, a different thing to go fix.
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'proxy_failed', detail: { error_kind: 'unreachable' } });

    expect(screen.getByText('Proxy unreachable')).toBeInTheDocument();
    expect(screen.queryByText(/Service temporarily unavailable/i)).toBeNull();
  });

  it('leaves proxy_failed alone even when a reason rides along', async () => {
    // Same exclusion as above, for the identity tier: `proxy_failed` already
    // names the thing to go fix, and it is not our browser service either.
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'proxy_failed', detail: { reason: 'identity_unresolved' } });

    expect(screen.getByText('Proxy unreachable')).toBeInTheDocument();
    expect(screen.queryByText(/Signed in, but the account is unidentified/i)).toBeNull();
  });

  it('reports success to the caller', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'success' });
    expect(screen.getByText('Account linked')).toBeInTheDocument();
    expect(onBound).toHaveBeenCalled();
    // Terminal — nothing left to cancel.
    expect(screen.queryByRole('button', { name: /Get a new code/i })).toBeNull();
  });

  it('reports success exactly once across parent re-renders', async () => {
    // Callers pass inline arrows, so every parent render hands us new callback
    // identities. Keying the success effect on them would re-fire onBound
    // after the reload it triggers — an endless reload loop.
    const node = (
      <SessionLoginModal
        platform="douyin"
        scopeType="user"
        scopeId="self"
        onClose={() => onClose()}
        onBound={() => onBound()}
      />
    );
    const view = render(node);
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'success' });
    expect(onBound).toHaveBeenCalledTimes(1);
    await act(async () => {
      view.rerender(
        <SessionLoginModal
          platform="douyin"
          scopeType="user"
          scopeId="self"
          onClose={() => onClose()}
          onBound={() => onBound()}
        />,
      );
    });
    expect(onBound).toHaveBeenCalledTimes(1);
  });

  it('cancels the pending login when the user closes mid-scan', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'waiting_scan', qrcode_data_url: QR });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    });
    expect(cancelSessionLogin).toHaveBeenCalledWith('task-1');
    expect(onClose).toHaveBeenCalled();
  });

  it('does not cancel a login that already finished', async () => {
    mount();
    await waitFor(() => expect(updateHandler).toBeTruthy());
    await pushLogin({ status: 'failed' });
    // Both the header X and the footer button are labelled "Close" — the
    // footer one is the last in DOM order.
    const closers = screen.getAllByRole('button', { name: /^Close$/i });
    await act(async () => {
      fireEvent.click(closers[closers.length - 1]);
    });
    expect(cancelSessionLogin).not.toHaveBeenCalled();
  });

  it('shows a retryable error when the start request itself fails', async () => {
    startSessionLogin.mockRejectedValueOnce(new Error('500'));
    mount();
    expect(await screen.findByText(/Could not start sign-in/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Get a new code/i })).toBeInTheDocument();
  });

  it('does not offer a retry when the server has no browser service', async () => {
    // 503 is the endpoint's deliberate fail-fast on a missing
    // BROWSER_SERVICE_URL / token. Retrying can never succeed, so offering the
    // button would just farm clicks on a problem the user cannot fix.
    startSessionLogin.mockRejectedValueOnce(
      Object.assign(new Error('distribution api failed: 503'), { status: 503 }),
    );
    mount();
    expect(await screen.findByText(/not set up on this server/i)).toBeInTheDocument();
    expect(screen.getByText(/Retrying will not help/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Get a new code/i })).toBeNull();
    expect(screen.queryByText(/Could not start sign-in/i)).toBeNull();
    // Nothing was ever dispatched, so there is nothing to "Cancel".
    expect(screen.queryByRole('button', { name: /^Cancel$/i })).toBeNull();
  });
});
