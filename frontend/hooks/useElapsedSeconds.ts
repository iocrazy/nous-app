/**
 * Live-ticking seconds counter from `startedAt` to now, in whole seconds.
 *
 * Used by AgentRunEvent to show "working for Xs" while an agent run is
 * in flight (msg.duration_seconds is null until the run completes).
 *
 * Cancelled when `enabled` flips false (timer cleared) or on unmount.
 * Returns 0 when startedAt is missing or malformed.
 */
import { useEffect, useState } from 'react';

export function useElapsedSeconds(
  startedAt: string | null | undefined,
  opts: { enabled: boolean },
): number {
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (!opts.enabled || !startedAt) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [opts.enabled, startedAt]);

  if (!startedAt) return 0;
  const t = Date.parse(startedAt);
  if (Number.isNaN(t)) return 0;
  return Math.max(0, Math.floor((now - t) / 1000));
}
