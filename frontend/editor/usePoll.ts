/**
 * usePoll — a minimal "dispatch a slow workflow, then poll until its output
 * materialises" driver, generalised over WHAT is polled.
 *
 * `useConvertPoll` observes the scenes+chapters lists (its predicate is fixed to
 * that shape). The storyboard's Auto Storyboard workflow instead lands its
 * output in a scene's SHOT list, which `useConvertPoll` cannot see. Rather than
 * widen that hook's contract for every caller, this primitive takes an async
 * `probe` that fetches whatever the caller cares about and returns whether the
 * end state has been reached. Same cadence / attempt cap / unmount-cleanup
 * semantics; no realtime subscription needed.
 */
import { useCallback, useEffect, useRef } from 'react';

/** Poll cadence + cap while waiting for a workflow's output to appear. */
export const POLL_INTERVAL_MS = 5000;
export const POLL_MAX_ATTEMPTS = 12;

/** Fetch the authoritative state and report whether the workflow has settled. */
export type PollProbe = () => Promise<boolean>;
/** Fires once: `settled=true` when the probe matched, `false` on cap reached. */
export type PollSettled = (settled: boolean) => void;

export interface Poll {
  startPoll: (probe: PollProbe, onSettled?: PollSettled) => void;
}

export function usePoll(): Poll {
  const timersRef = useRef<Set<ReturnType<typeof setInterval>>>(new Set());

  // Stop any in-flight polls when the owning component unmounts.
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((timer) => clearInterval(timer));
      timers.clear();
    };
  }, []);

  const startPoll = useCallback<Poll['startPoll']>((probe, onSettled) => {
    let attempts = 0;
    const timers = timersRef.current;
    const timer = setInterval(async () => {
      attempts += 1;
      let matched = false;
      try {
        matched = await probe();
      } catch (err) {
        // A transient fetch failure still counts toward the cap so a persistent
        // error can't leak the interval or pin a caller on its busy state.
        console.error('[usePoll] probe failed', err);
      }
      if (matched || attempts >= POLL_MAX_ATTEMPTS) {
        clearInterval(timer);
        timers.delete(timer);
        onSettled?.(matched);
      }
    }, POLL_INTERVAL_MS);
    timers.add(timer);
  }, []);

  return { startPoll };
}
