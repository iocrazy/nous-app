import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import type { SessionLoginState } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const startSessionLogin = vi.fn();
const submitSmsCode = vi.fn();
const cancelSessionLogin = vi.fn();

// Spread the real module so SMS_CODE_PATTERN stays the one the service and
// the backend agree on — a hand-copied literal here would let the submit gate
// drift away from what the endpoint accepts without any test noticing.
vi.mock('../../services/distributionService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/distributionService')>()),
  startSessionLogin: (...a: unknown[]) => startSessionLogin(...a),
  submitSmsCode: (...a: unknown[]) => submitSmsCode(...a),
  cancelSessionLogin: (...a: unknown[]) => cancelSessionLogin(...a),
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
    // The server-authored detail is shown verbatim, not swallowed.
    expect(screen.getByText('Code sent to 138****0000')).toBeInTheDocument();

    const input = screen.getByLabelText(/Verification code/i);
    fireEvent.change(input, { target: { value: '123456' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Submit$/i }));
    });
    expect(submitSmsCode).toHaveBeenCalledWith('task-1', '123456');
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
    // status alone would swallow the platform's "no".
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
    expect(await screen.findByText('Wrong verification code')).toBeInTheDocument();
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
});
