/**
 * What a run's OWN transcript says about it, for the row header above its
 * trajectory (harness 2b-1 §2 / §3).
 *
 * The Cockpit reads `rollup.current_run.view` — which is gone the moment the
 * issue goes idle, so a forked run that finished in 30 s had no "Forked from"
 * entry anywhere, and a timed-out tool lost its "1 timed out" count (real
 * stack, 2026-09-09). Both facts are in the run's events, which outlive the
 * live view, so the row derives them itself.
 */
import type { AgentRunEvent } from '../../types';

export interface ForkOrigin {
  ofRunId: string;
  atSeq: number;
}

/** The `fork` event a forked run opens with (T2), or null for a root run. */
export function forkOrigin(events: readonly AgentRunEvent[]): ForkOrigin | null {
  for (const ev of events) {
    if (ev.event_type !== 'fork') continue;
    const p = ev.payload ?? {};
    const of = p.of_run_id;
    const at = Number(p.at_seq);
    if ((typeof of === 'string' || typeof of === 'number') && String(of) && Number.isFinite(at) && at > 0) {
      return { ofRunId: String(of), atSeq: at };
    }
    return null;
  }
  return null;
}

export interface TimedOutTools {
  count: number;
  /** Name of the last tool that timed out, for the chip's title. */
  last: string | null;
}

/** Tool calls whose result carries `timed_out: true` (T4 wrapper contract). */
export function timedOutTools(events: readonly AgentRunEvent[]): TimedOutTools {
  let count = 0;
  let last: string | null = null;
  for (const ev of events) {
    if (ev.event_type !== 'tool_call') continue;
    const result = ev.payload?.result;
    if (result && typeof result === 'object' && (result as { timed_out?: unknown }).timed_out === true) {
      count += 1;
      const tool = ev.payload?.tool;
      last = typeof tool === 'string' && tool ? tool : last;
    }
  }
  return { count, last };
}
