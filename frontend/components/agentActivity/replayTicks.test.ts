import { describe, expect, it } from 'vitest';
import { replayTicks } from './replayTicks';

const ev = (seq: number, event_type: string, payload: Record<string, unknown> = {}) =>
  ({ seq, event_type, payload, created_at: '' }) as never;

describe('replayTicks', () => {
  it('keeps only step_start and turn_end, in seq order, with coordinates', () => {
    const ticks = replayTicks([
      ev(9, 'turn_end', { reason: 'completed' }),
      ev(1, 'user'),
      ev(2, 'step_start', { turn: 1, step: 1 }),
      ev(3, 'tool_call'),
      ev(4, 'step_start', { turn: 1, step: 2 }),
    ]);
    expect(ticks).toEqual([
      { seq: 2, kind: 'step', turn: 1, step: 1 },
      { seq: 4, kind: 'step', turn: 1, step: 2 },
      { seq: 9, kind: 'turn_end', turn: null, step: null },
    ]);
  });
  it('is empty for a run with no steps yet', () => {
    expect(replayTicks([ev(1, 'user')])).toEqual([]);
  });
  it('treats non-numeric coordinates as null', () => {
    expect(replayTicks([ev(2, 'step_start', { turn: '1', step: NaN })])).toEqual([
      { seq: 2, kind: 'step', turn: null, step: null },
    ]);
  });
});
