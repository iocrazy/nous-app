/**
 * The last hop of the mid-publish verification-code channel.
 *
 * Everything behind this file can be correct and the feature still be useless:
 * a platform asks for a code, the browser parks the publish waiting for one,
 * and if nothing puts an input on screen the post dies exactly as it did before
 * the channel existed. So these tests are about what a person can *see and do*.
 *
 * Two assertions carry most of the weight, because they are the ones a
 * plausible-looking implementation gets wrong:
 *
 * 1. **A refused code leaves the field filled and usable.** The natural thing
 *    to write is `setCode('')` after every submit. A refusal is almost always
 *    one mistyped digit, and clearing sends the user back to their SMS app to
 *    re-read all of them — so the "cost of a typo" is the whole post if the
 *    panel also closes.
 * 2. **A failed poll is not "nothing to do here".** `PublishPage`'s `imagesGate`
 *    collapses loading / failed / genuinely-unsupported into one value and then
 *    renders an assertion about the platform. Here that same collapse would
 *    hide the one prompt that mattered, so `unavailable` and `quiet` are
 *    separate states and the test names them separately.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { PublishSmsPrompt } from './PublishSmsPrompt';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string, o?: Record<string, unknown>) => {
      const base = d ?? _k;
      return o
        ? base.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(o[name] ?? ''))
        : base;
    },
  }),
}));

const getPublishSmsState = vi.fn();
const submitPublishSmsCode = vi.fn();

vi.mock('../../services/distributionService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/distributionService')>()),
  getPublishSmsState: (...a: unknown[]) => getPublishSmsState(...a),
  submitPublishSmsCode: (...a: unknown[]) => submitPublishSmsCode(...a),
}));

const waitingState = (over: Record<string, unknown> = {}) => ({
  waiting: true,
  account_id: 7,
  platform: 'douyin',
  attempts_left: 3,
  max_attempts: 3,
  seconds_remaining: 170.0,
  outcome: null,
  message: '',
  ...over,
});

const quietState = () => ({
  waiting: false,
  attempts_left: 0,
  max_attempts: 0,
  seconds_remaining: 0,
  outcome: null,
  message: '',
});

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('PublishSmsPrompt', () => {
  it('puts an input on screen when a publish is parked on a code', async () => {
    getPublishSmsState.mockResolvedValue(waitingState());

    render(<PublishSmsPrompt taskId={11} active />);

    expect(await screen.findByLabelText('Verification code')).toBeTruthy();
    expect(screen.getByText(/Verification code required/)).toBeTruthy();
    // The remaining budget is the user's, so it has to be on screen.
    expect(screen.getByText(/3 attempt\(s\) left/)).toBeTruthy();
  });

  it('stays silent while a publish is not asking for anything', async () => {
    getPublishSmsState.mockResolvedValue(quietState());

    render(<PublishSmsPrompt taskId={11} active />);

    await waitFor(() => expect(getPublishSmsState).toHaveBeenCalled());
    expect(screen.queryByLabelText('Verification code')).toBeNull();
  });

  it('stays silent — and asserts nothing — when the poll itself fails', async () => {
    // The `imagesGate` trap: a failed request must not be rendered as a claim
    // about the publish. Silence is the honest output; a "no code needed"
    // message would be a statement we have no evidence for.
    getPublishSmsState.mockRejectedValue(new Error('network down'));

    render(<PublishSmsPrompt taskId={11} active />);

    await waitFor(() => expect(getPublishSmsState).toHaveBeenCalled());
    expect(screen.queryByLabelText('Verification code')).toBeNull();
    expect(screen.queryByText(/not asking/i)).toBeNull();
    expect(screen.queryByText(/no code/i)).toBeNull();
  });

  it('never polls a batch that is not running', async () => {
    render(<PublishSmsPrompt taskId={11} active={false} />);
    await Promise.resolve();
    expect(getPublishSmsState).not.toHaveBeenCalled();
  });

  it('keeps the typed code and the input alive when the platform refuses it', async () => {
    // The load-bearing one. `retryable: true` means the publish is still parked
    // on the same page, so the next code lands where this one did.
    getPublishSmsState.mockResolvedValue(waitingState());
    submitPublishSmsCode.mockResolvedValue({
      outcome: 'rejected',
      message: 'not accepted',
      attempts_left: 2,
      retryable: true,
    });

    render(<PublishSmsPrompt taskId={11} active />);
    const input = (await screen.findByLabelText('Verification code')) as HTMLInputElement;

    fireEvent.change(input, { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit/ }));

    await waitFor(() => expect(submitPublishSmsCode).toHaveBeenCalledWith(11, '123456'));

    // A refusal is a verdict the user must SEE...
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByText(/not accepted and the platform is still asking/i)).toBeTruthy();
    // ...and be able to act on, in place. Clearing the field here would be the
    // regression: the value survives and the control is still enabled.
    expect(input.value).toBe('123456');
    expect(input.disabled).toBe(false);
  });

  it('distinguishes "could not deliver" from "your code was wrong"', async () => {
    // Opposite remedies — retype vs wait and retry — so collapsing them lets a
    // container hiccup accuse a perfectly good code.
    getPublishSmsState.mockResolvedValue(waitingState());
    submitPublishSmsCode.mockRejectedValue(new Error('browser unreachable'));

    render(<PublishSmsPrompt taskId={11} active />);
    const input = await screen.findByLabelText('Verification code');

    fireEvent.change(input, { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit/ }));

    expect(await screen.findByText(/could not be delivered/i)).toBeTruthy();
    expect(screen.queryByText(/was not accepted/i)).toBeNull();
  });

  it('closes the panel once the code is accepted', async () => {
    getPublishSmsState.mockResolvedValue(waitingState());
    submitPublishSmsCode.mockResolvedValue({
      outcome: 'accepted',
      message: 'ok',
      attempts_left: 2,
      retryable: false,
    });

    render(<PublishSmsPrompt taskId={11} active />);
    const input = await screen.findByLabelText('Verification code');

    fireEvent.change(input, { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit/ }));

    await waitFor(() => expect(screen.queryByLabelText('Verification code')).toBeNull());
    // `accepted` needs no notice of its own — the publish carried on and the
    // batch row says so.
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('explains a challenge that ran out of time instead of just vanishing', async () => {
    getPublishSmsState.mockResolvedValue({
      ...quietState(),
      outcome: 'expired',
      message: 'window closed',
    });

    render(<PublishSmsPrompt taskId={11} active />);

    expect(await screen.findByText(/No code was entered in time/i)).toBeTruthy();
  });

  it('refuses to submit a code the server would 422 on', async () => {
    getPublishSmsState.mockResolvedValue(waitingState());

    render(<PublishSmsPrompt taskId={11} active />);
    const input = await screen.findByLabelText('Verification code');

    fireEvent.change(input, { target: { value: '12' } });
    const button = screen.getByRole('button', { name: /Submit/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);

    fireEvent.click(button);
    expect(submitPublishSmsCode).not.toHaveBeenCalled();
  });

  it('strips non-digits as they are typed, paste included', async () => {
    getPublishSmsState.mockResolvedValue(waitingState());

    render(<PublishSmsPrompt taskId={11} active />);
    const input = (await screen.findByLabelText('Verification code')) as HTMLInputElement;

    fireEvent.change(input, { target: { value: 'a1b2c3d4e5f6' } });
    expect(input.value).toBe('123456');
  });
});
