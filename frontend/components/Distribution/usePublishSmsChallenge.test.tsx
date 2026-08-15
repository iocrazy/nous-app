/**
 * The hook's *state*, tested where the component cannot see it.
 *
 * This file exists because of a hole found by running the reverse check the
 * repo asks for. `PublishSmsPrompt.test.tsx` covers what a user sees — and
 * "the poll failed" and "the server says nothing is waiting" both render
 * nothing, so **every component-level test passed with the two collapsed into
 * one state.** The guard against the `imagesGate` mistake was decorative: the
 * distinction existed in the source and nothing held it there.
 *
 * A test that cannot fail on the bug it names is worse than no test, because it
 * is counted as coverage. So the distinction is asserted on the value that
 * carries it.
 *
 * Falsifiability, checked by hand and recorded here so the next person does not
 * have to re-derive it: replacing `unavailable` with `quiet` in the hook's catch
 * block turns the first test below red, and leaves all ten component tests
 * green.
 */

import { act, render } from '@testing-library/react';
import React from 'react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { usePublishSmsChallenge, type PublishSmsPhase } from './usePublishSmsChallenge';

const getPublishSmsState = vi.fn();
const submitPublishSmsCode = vi.fn();

vi.mock('../../services/distributionService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/distributionService')>()),
  getPublishSmsState: (...a: unknown[]) => getPublishSmsState(...a),
  submitPublishSmsCode: (...a: unknown[]) => submitPublishSmsCode(...a),
}));

/** Renders the hook and records every phase it passes through. */
function observePhases(taskId: number | null, active: boolean) {
  const seen: PublishSmsPhase[] = [];
  const Probe: React.FC = () => {
    const { phase } = usePublishSmsChallenge(taskId, active);
    seen.push(phase);
    return null;
  };
  render(<Probe />);
  return seen;
}

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

beforeEach(() => vi.clearAllMocks());

describe('usePublishSmsChallenge phases', () => {
  it('reports a failed poll as `unavailable`, never as `quiet`', async () => {
    // The whole point. `quiet` means "the server told us nothing is waiting" —
    // an actual answer. A network error is not an answer, and treating it as
    // one is how the prompt a user was waiting for silently never arrives.
    getPublishSmsState.mockRejectedValue(new Error('network down'));

    const seen = observePhases(11, true);
    await flush();

    const kinds = seen.map((p) => p.kind);
    expect(kinds).toContain('unavailable');
    expect(kinds).not.toContain('quiet');
  });

  it('reports "the server answered, nothing is waiting" as `quiet`', async () => {
    getPublishSmsState.mockResolvedValue({
      waiting: false, attempts_left: 0, max_attempts: 0,
      seconds_remaining: 0, outcome: null, message: '',
    });

    const seen = observePhases(11, true);
    await flush();

    const kinds = seen.map((p) => p.kind);
    expect(kinds).toContain('quiet');
    expect(kinds).not.toContain('unavailable');
  });

  it('passes through `checking` before any answer arrives', async () => {
    // The third state the `imagesGate` shape loses. "We have not heard back
    // yet" must not be renderable as a claim about the publish either.
    let resolve: (v: unknown) => void = () => {};
    getPublishSmsState.mockReturnValue(new Promise((r) => { resolve = r; }));

    const seen = observePhases(11, true);
    expect(seen.map((p) => p.kind)).toContain('checking');
    expect(seen.map((p) => p.kind)).not.toContain('quiet');

    await act(async () => {
      resolve({
        waiting: false, attempts_left: 0, max_attempts: 0,
        seconds_remaining: 0, outcome: null, message: '',
      });
      await Promise.resolve();
    });
  });

  it('stays `idle` — and issues no request — for a batch that is not running', async () => {
    const seen = observePhases(11, false);
    await flush();

    expect(seen.every((p) => p.kind === 'idle')).toBe(true);
    expect(getPublishSmsState).not.toHaveBeenCalled();
  });

  it('keeps a refused code retryable rather than ending the challenge', async () => {
    // `retryable` is taken from the browser's answer and never re-derived here.
    // Re-deriving it is one more chance to disagree with the process that owns
    // the truth, and the disagreement reads "you can try again" beside a
    // publish that already gave up.
    getPublishSmsState.mockResolvedValue({
      waiting: true, account_id: 7, platform: 'douyin', attempts_left: 3,
      max_attempts: 3, seconds_remaining: 170, outcome: null, message: '',
    });
    submitPublishSmsCode.mockResolvedValue({
      outcome: 'rejected', message: 'nope', attempts_left: 2, retryable: true,
    });

    const seen: PublishSmsPhase[] = [];
    let submit: ((c: string) => Promise<unknown>) | null = null;
    const Probe: React.FC = () => {
      const hook = usePublishSmsChallenge(11, true);
      seen.push(hook.phase);
      submit = hook.submit;
      return null;
    };
    render(<Probe />);
    await flush();

    await act(async () => { await submit!('123456'); });

    // Still parked: the publish is on the same page waiting for another code.
    expect(seen[seen.length - 1].kind).toBe('waiting');
  });

  it('ends the challenge when the verdict is not retryable', async () => {
    getPublishSmsState.mockResolvedValue({
      waiting: true, account_id: 7, platform: 'douyin', attempts_left: 1,
      max_attempts: 3, seconds_remaining: 90, outcome: null, message: '',
    });
    submitPublishSmsCode.mockResolvedValue({
      outcome: 'exhausted', message: 'no attempts left', attempts_left: 0, retryable: false,
    });

    const seen: PublishSmsPhase[] = [];
    let submit: ((c: string) => Promise<unknown>) | null = null;
    const Probe: React.FC = () => {
      const hook = usePublishSmsChallenge(11, true);
      seen.push(hook.phase);
      submit = hook.submit;
      return null;
    };
    render(<Probe />);
    await flush();

    await act(async () => { await submit!('999999'); });

    const last = seen[seen.length - 1];
    expect(last.kind).toBe('ended');
    expect(last.kind === 'ended' && last.outcome).toBe('exhausted');
  });
});
