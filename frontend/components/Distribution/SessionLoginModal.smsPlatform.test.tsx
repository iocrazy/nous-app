/**
 * The modal on a platform that has **no QR code at all**.
 *
 * `SessionLoginModal.test.tsx` covers the scan shape; this file covers the one
 * the modal used to draw wrongly. Xiaohongshu's creator platform signs in with
 * 手机号 + 验证码 and renders nothing to scan ([实测 2026-08-08]), yet every user
 * who clicked "connect Xiaohongshu" got a QR placeholder and a spinner waiting
 * for an image the backend could not produce.
 *
 * Which shape is drawn comes from `GET /distribution/capabilities`
 * (`login_method`) — so the pair of tests at the top is the reverse
 * verification the fix needs: flip the capability and the other shape has to
 * appear. A modal that always drew the QR frame would fail the first test, and
 * one that always drew the phone form would fail the second.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import type { SessionLoginState } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string, o?: Record<string, unknown>) => {
    const base = d ?? _k;
    return o
      ? base.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(o[name] ?? ''))
      : base;
  } }),
}));

const startSessionLogin = vi.fn();
const submitLoginPhone = vi.fn();
const submitSmsCode = vi.fn();
const cancelSessionLogin = vi.fn();
const getPlatformCapabilities = vi.fn();

vi.mock('../../services/distributionService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/distributionService')>()),
  startSessionLogin: (...a: unknown[]) => startSessionLogin(...a),
  submitLoginPhone: (...a: unknown[]) => submitLoginPhone(...a),
  submitSmsCode: (...a: unknown[]) => submitSmsCode(...a),
  cancelSessionLogin: (...a: unknown[]) => cancelSessionLogin(...a),
  getPlatformCapabilities: () => getPlatformCapabilities(),
}));

let updateHandler: ((payload: { new: unknown }) => void) | null = null;
const removeChannel = vi.fn();
const seedRow = { data: null as unknown, error: null as unknown };

vi.mock('../../supabaseClient', () => {
  const channelObj = {
    on: (_evt: string, _cfg: unknown, cb: (p: { new: unknown }) => void) => {
      updateHandler = cb;
      return channelObj;
    },
    subscribe: (cb: (s: string) => void) => {
      cb('SUBSCRIBED');
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

const mount = (platform = 'xiaohongshu') =>
  render(
    <SessionLoginModal
      platform={platform}
      scopeType="user"
      scopeId="self"
      onClose={onClose}
      onBound={onBound}
    />,
  );

/** Deliver a `metadata.login` patch the way the workflow writes it. */
const pushLogin = async (login: Partial<SessionLoginState>, platform = 'xiaohongshu') => {
  await act(async () => {
    updateHandler?.({ new: { metadata: { login: { platform, ...login } } } });
  });
};

/** Let the capabilities promise settle before asserting on the copy. */
const settle = async () => {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
};

describe('SessionLoginModal on an SMS-only platform', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateHandler = null;
    seedRow.data = null;
    startSessionLogin.mockResolvedValue({ task_id: 'task-1' });
    submitLoginPhone.mockResolvedValue({
      success: true, status: 'sms_required', message: 'ok', detail: { code_requested: true },
    });
    cancelSessionLogin.mockResolvedValue({ cancelled: true, context_released: true, message: 'ok' });
    getPlatformCapabilities.mockResolvedValue({
      xiaohongshu: { platform: 'xiaohongshu', login_method: 'sms' },
      douyin: { platform: 'douyin', login_method: 'qrcode' },
    });
  });

  // ── reverse verification: the shape follows the capability ──────────────

  it('never promises a QR code when the platform declares SMS sign-in', async () => {
    mount();
    await settle();

    // The heading and the subtitle are the two lines that made the promise.
    expect(screen.queryByText('Sign in with QR code')).toBeNull();
    expect(screen.getByText('Sign in with a verification code')).toBeTruthy();
    expect(screen.getByText(/has no code to scan/)).toBeTruthy();
    // And the "we are fetching the image" wait, which is the part the user
    // reported: a placeholder that never resolves.
    expect(screen.queryByText('Fetching the QR code')).toBeNull();
    expect(screen.getByText('Opening the sign-in page')).toBeTruthy();
  });

  it('still draws the scan shape when the platform declares a QR sign-in', async () => {
    // The other half of the pin. Reverting the component to "always QR" keeps
    // this green and turns the previous test red; reverting it to "always
    // phone" does the opposite. Neither can pass by accident.
    mount('douyin');
    await settle();

    expect(screen.getByText('Sign in with QR code')).toBeTruthy();
    expect(screen.getByText(/Scan with the/)).toBeTruthy();
    expect(screen.queryByText('Sign in with a verification code')).toBeNull();
  });

  it('stays neutral rather than guessing when capabilities cannot be read', async () => {
    // A failed capability read must not fall back to the QR shape — falling
    // back is the behaviour being removed. It keeps the default copy, and the
    // server statuses still drive everything the user can act on.
    getPlatformCapabilities.mockRejectedValue(new Error('offline'));
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    mount();
    await settle();

    expect(errors).toHaveBeenCalled();
    expect(screen.queryByRole('textbox')).toBeNull();
    errors.mockRestore();
  });

  // ── the phone step ──────────────────────────────────────────────────────

  it('asks for the phone number and says nothing has been sent yet', async () => {
    mount();
    await settle();
    await pushLogin({ status: 'phone_required', message: 'waiting for the phone number' });

    expect(screen.getByText('Phone number needed')).toBeTruthy();
    expect(screen.getByLabelText('Phone number')).toBeTruthy();
    // The copy may not imply a message is on its way: the platform has not been
    // given a number, so no text can exist. This sentence is the whole fix.
    expect(screen.getByText(/we will ask the platform to text a verification code/)).toBeTruthy();
    expect(screen.queryByLabelText('Verification code')).toBeNull();
  });

  it('sends the number and moves on to the code once the platform is texting', async () => {
    mount();
    await settle();
    await pushLogin({ status: 'phone_required', message: 'waiting for the phone number' });

    fireEvent.change(screen.getByLabelText('Phone number'), {
      target: { value: '138 0000 0000' },
    });
    // Spaces are stripped as typed — the backend 422s on anything but digits,
    // and a 422 body is not a `SessionOpResult`.
    expect((screen.getByLabelText('Phone number') as HTMLInputElement).value)
      .toBe('13800000000');

    fireEvent.click(screen.getByText('Send code'));
    await waitFor(() => expect(submitLoginPhone).toHaveBeenCalledWith('task-1', '13800000000'));

    await pushLogin({
      status: 'sms_required',
      message: 'the platform is asking for a verification code',
      detail: { code_requested: true },
    });
    expect(screen.getByLabelText('Verification code')).toBeTruthy();
    // Only now, and only because the browser service latched its own click.
    expect(screen.getByText(/We asked the platform to text a code/)).toBeTruthy();
  });

  it('refuses to submit a number the backend would reject', async () => {
    mount();
    await settle();
    await pushLogin({ status: 'phone_required', message: 'waiting for the phone number' });

    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '138' } });
    fireEvent.click(screen.getByText('Send code'));

    expect(submitLoginPhone).not.toHaveBeenCalled();
    expect(screen.getByText('Enter the phone number, digits only')).toBeTruthy();
  });

  // ── typed failures, because every selector here is unverified ───────────

  it('says the page had no number field instead of looking like it is waiting', async () => {
    submitLoginPhone.mockResolvedValue({
      success: false,
      status: 'phone_required',
      message: 'no phone number field was found on the sign-in page',
      detail: { reason: 'phone_input_missing' },
    });
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    mount();
    await settle();
    await pushLogin({ status: 'phone_required', message: 'waiting for the phone number' });

    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '13800000000' } });
    fireEvent.click(screen.getByText('Send code'));

    await waitFor(() => expect(
      screen.getByText(/did not show a phone number field/),
    ).toBeTruthy());
    // The typed number stays put: the next action is to correct it, not retype
    // eleven digits.
    expect((screen.getByLabelText('Phone number') as HTMLInputElement).value)
      .toBe('13800000000');
    errors.mockRestore();
  });

  it('says no code was sent when the platform button did not respond', async () => {
    // The distinction that matters: this one is NOT "your number is wrong", and
    // it is NOT "wait for your text". Nothing was sent.
    submitLoginPhone.mockResolvedValue({
      success: false,
      status: 'phone_required',
      message: 'the send-code control could not be pressed',
      detail: { reason: 'code_request_failed' },
    });
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    mount();
    await settle();
    await pushLogin({ status: 'phone_required', message: 'waiting for the phone number' });

    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '13800000000' } });
    fireEvent.click(screen.getByText('Send code'));

    await waitFor(() => expect(screen.getByText(/no code was sent/)).toBeTruthy());
    expect(screen.queryByLabelText('Verification code')).toBeNull();
    errors.mockRestore();
  });

  it('flips to "start over" rather than leaving a dead form up on a 409', async () => {
    submitLoginPhone.mockRejectedValue(Object.assign(new Error('gone'), { status: 409 }));
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    mount();
    await settle();
    await pushLogin({ status: 'phone_required', message: 'waiting for the phone number' });

    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '13800000000' } });
    fireEvent.click(screen.getByText('Send code'));

    await waitFor(() => expect(screen.getByText('This sign-in has ended')).toBeTruthy());
    expect(screen.queryByLabelText('Phone number')).toBeNull();
    // "Get a new code" would be nonsense here — there is no code to get.
    expect(screen.getByText('Start over')).toBeTruthy();
    errors.mockRestore();
  });
});
