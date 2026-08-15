import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getPublishSmsState,
  submitPublishSmsCode,
  type PublishSmsVerdict,
} from '../../services/distributionService';

/**
 * Watch one running publish for "the platform is asking for a verification code".
 *
 * The publish itself is one blocking call inside a workflow step, so nothing on
 * that path can tell the user it stopped to ask for something. A watcher on the
 * backend mirrors the state into `task_tracking.metadata`; this hook is the last
 * hop that puts it on screen.
 *
 * --- why the state is a union and not a boolean ------------------------------
 *
 * `PublishPage`'s `imagesGate` collapses "still loading", "the request failed"
 * and "the platform genuinely does not support this" into one value, and then
 * renders a sentence asserting the third. That is the bug this file is written
 * to not repeat.
 *
 * So the four *non-waiting* situations stay distinguishable:
 *
 * - `idle`     — we are not watching (the batch is not running)
 * - `checking` — watching, no answer back yet. **Not** "no code needed"
 * - `unavailable` — the poll itself failed. **Not** "no code needed"
 * - `quiet`    — the server answered, and nothing is waiting
 *
 * Only `quiet` licenses "this publish is not asking for anything". The other
 * three license silence, which is the honest thing to show when we do not know.
 */
export type PublishSmsPhase =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'unavailable' }
  | { kind: 'quiet' }
  | {
      kind: 'waiting';
      platform: string | null;
      attemptsLeft: number;
      maxAttempts: number;
      secondsRemaining: number;
    }
  | { kind: 'ended'; outcome: string; message: string };

/** How often to ask. The window is 180s, so this is ~60 samples at most. */
const POLL_MS = 3000;

export interface PublishSmsChallenge {
  phase: PublishSmsPhase;
  /** The last verdict a submission produced, or null before the first one. */
  verdict: PublishSmsVerdict | null;
  submitting: boolean;
  submit: (code: string) => Promise<PublishSmsVerdict | null>;
  /** Drop the last verdict — used when the user starts retyping after a refusal. */
  clearVerdict: () => void;
}

export function usePublishSmsChallenge(
  taskId: number | string | null,
  active: boolean,
): PublishSmsChallenge {
  const [phase, setPhase] = useState<PublishSmsPhase>({ kind: 'idle' });
  const [verdict, setVerdict] = useState<PublishSmsVerdict | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // A submission and a poll race by nature: the poll can land between "the code
  // was accepted" and the next render and would flip the panel back to
  // `waiting` for one tick. Suppressing polls while a submit is in flight keeps
  // the user from watching the box they just used flicker.
  const busy = useRef(false);

  useEffect(() => {
    if (!active || taskId === null || taskId === undefined) {
      setPhase({ kind: 'idle' });
      return undefined;
    }

    let cancelled = false;
    // Announce that we are looking *before* the first answer, so the UI can
    // stay silent rather than assert anything it has not been told.
    setPhase((prev) => (prev.kind === 'idle' ? { kind: 'checking' } : prev));

    const tick = async () => {
      if (busy.current) return;
      try {
        const state = await getPublishSmsState(taskId);
        if (cancelled) return;
        if (state.waiting) {
          setPhase({
            kind: 'waiting',
            platform: state.platform ?? null,
            attemptsLeft: state.attempts_left,
            maxAttempts: state.max_attempts,
            secondsRemaining: state.seconds_remaining,
          });
        } else if (state.outcome) {
          setPhase({ kind: 'ended', outcome: state.outcome, message: state.message });
        } else {
          setPhase({ kind: 'quiet' });
        }
      } catch (err) {
        if (cancelled) return;
        // Distinct from `quiet` on purpose. A failed poll means we do not know
        // whether a code is wanted — rendering "nothing to do here" off a
        // network error is how a user misses the one prompt that mattered.
        console.error('distribution: publish sms poll failed', err);
        setPhase({ kind: 'unavailable' });
      }
    };

    void tick();
    const timer = window.setInterval(() => { void tick(); }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [taskId, active]);

  const submit = useCallback(
    async (code: string): Promise<PublishSmsVerdict | null> => {
      if (taskId === null || taskId === undefined) return null;
      busy.current = true;
      setSubmitting(true);
      try {
        const result = await submitPublishSmsCode(taskId, code);
        setVerdict(result);
        // A refused code leaves the panel open: the publish is still parked on
        // the same page, so the next code lands where this one did. Anything
        // else ends the challenge, and the poll will confirm that shortly.
        if (!result.retryable) {
          setPhase({ kind: 'ended', outcome: result.outcome, message: result.message });
        }
        return result;
      } catch (err) {
        console.error('distribution: publish sms submit failed', err);
        // Typed as `unreachable`, never as `rejected`: we do not know the code
        // was wrong, and telling someone their correct code was refused is a
        // worse lie than telling them we could not deliver it.
        const failure: PublishSmsVerdict = {
          outcome: 'unreachable',
          message: '',
          attempts_left: 0,
          retryable: true,
        };
        setVerdict(failure);
        return failure;
      } finally {
        busy.current = false;
        setSubmitting(false);
      }
    },
    [taskId],
  );

  const clearVerdict = useCallback(() => setVerdict(null), []);

  return { phase, verdict, submitting, submit, clearVerdict };
}
