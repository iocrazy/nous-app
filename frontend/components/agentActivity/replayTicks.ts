/**
 * Replay scrubber ticks (harness 2b-1 §1): the step boundaries of a run —
 * `step_start` and `turn_end` only — in seq order with their coordinates.
 * A fork may only land on one of these seqs (the backend rejects anything
 * else with `not_a_step_boundary`).
 */
import type { AgentRunEvent } from '../../types';

export interface Tick {
  seq: number;
  kind: 'step' | 'turn_end';
  turn: number | null;
  step: number | null;
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export function replayTicks(events: AgentRunEvent[]): Tick[] {
  return events
    .filter((e) => e.event_type === 'step_start' || e.event_type === 'turn_end')
    .slice()
    .sort((a, b) => a.seq - b.seq)
    .map((e) => ({
      seq: e.seq,
      kind: e.event_type === 'step_start' ? 'step' : 'turn_end',
      turn: num(e.payload?.turn),
      step: num(e.payload?.step),
    }));
}
