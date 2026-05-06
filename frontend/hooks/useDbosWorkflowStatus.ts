/**
 * React hook: subscribe to a DBOS workflow's status via SSE.
 *
 * Pass a `workflowId` (null/undefined = no subscription, hook returns
 * idle state). Returns the latest snapshot, terminal state flag, and
 * an `error` string for non-recoverable failures (auth, not_found,
 * timeout). Network blips are silently retried by EventSource.
 *
 * Usage:
 *   const { snapshot, isTerminal, isLoading, error } =
 *     useDbosWorkflowStatus(workflowId, { includeSteps: true });
 *
 *   if (isLoading) return <Spinner />;
 *   if (snapshot?.status === 'SUCCESS') return <Result data={snapshot.output} />;
 */

import { useEffect, useRef, useState } from 'react';
import {
  DbosWorkflowSnapshot,
  TERMINAL_STATES,
  subscribeWorkflow,
} from '../services/dbosWorkflowService';

export type DbosWorkflowError =
  | { kind: 'not_found' }
  | { kind: 'timeout' }
  | { kind: 'auth' | 'network'; message: string };

export interface UseDbosWorkflowStatus {
  /** Most recent status snapshot, or null while waiting for first push. */
  snapshot: DbosWorkflowSnapshot | null;
  /** True until the first snapshot arrives. */
  isLoading: boolean;
  /** True when status is one of {SUCCESS, ERROR, CANCELLED, ...}. */
  isTerminal: boolean;
  /** Non-recoverable failure. Network blips are auto-retried and don't
   *  surface here. */
  error: DbosWorkflowError | null;
}

export interface UseDbosWorkflowStatusOptions {
  includeSteps?: boolean;
}

export function useDbosWorkflowStatus(
  workflowId: string | null | undefined,
  opts: UseDbosWorkflowStatusOptions = {}
): UseDbosWorkflowStatus {
  const [snapshot, setSnapshot] = useState<DbosWorkflowSnapshot | null>(null);
  const [error, setError] = useState<DbosWorkflowError | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(Boolean(workflowId));
  const subscriptionId = useRef(0);

  useEffect(() => {
    if (!workflowId) {
      setSnapshot(null);
      setError(null);
      setIsLoading(false);
      return;
    }

    setSnapshot(null);
    setError(null);
    setIsLoading(true);

    const subId = ++subscriptionId.current;
    let cancelled = false;
    let unsubscribe: (() => void) | null = null;

    subscribeWorkflow(workflowId, {
      includeSteps: opts.includeSteps,
      onStatus: (snap) => {
        // Stale subscription guard: a rapid workflowId change could
        // race with this callback. subscriptionId.current ticks per
        // mount/dep change so we drop stale pushes.
        if (cancelled || subId !== subscriptionId.current) return;
        setSnapshot(snap);
        setIsLoading(false);
      },
      onDone: () => {
        // No-op — terminal status will already have been pushed via
        // onStatus before the server closes the stream.
      },
      onNotFound: () => {
        if (cancelled || subId !== subscriptionId.current) return;
        setError({ kind: 'not_found' });
        setIsLoading(false);
      },
      onTimeout: () => {
        if (cancelled || subId !== subscriptionId.current) return;
        setError({ kind: 'timeout' });
      },
      onError: (err) => {
        if (cancelled || subId !== subscriptionId.current) return;
        // EventSource native error doesn't tell us *why*. We mark
        // 'network' here; auth failures hit this path too. The 401
        // makes the EventSource go to readyState=CLOSED and stay
        // there, while transient errors auto-reconnect.
        const target = err.target as EventSource | null;
        if (target && target.readyState === EventSource.CLOSED) {
          setError({ kind: 'network', message: 'Stream closed' });
        }
      },
    })
      .then((u) => {
        if (cancelled) {
          u();
          return;
        }
        unsubscribe = u;
      })
      .catch((e) => {
        if (cancelled) return;
        setError({ kind: 'auth', message: String(e?.message ?? e) });
        setIsLoading(false);
      });

    return () => {
      cancelled = true;
      if (unsubscribe) unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId, opts.includeSteps]);

  const isTerminal = Boolean(
    snapshot?.status && TERMINAL_STATES.has(snapshot.status)
  );

  return { snapshot, isLoading, isTerminal, error };
}
