import { describe, expect, it } from 'vitest';

import type { AgentRunEvent } from '../../../types';
import { foldEvents, liveStep } from './foldEvents';

let seq = 0;
function ev(event_type: string, payload: Record<string, unknown> = {}, coords: { turn?: number; step?: number } = {}): AgentRunEvent {
  seq += 1;
  return { seq, event_type, payload, created_at: `2026-09-05T00:00:${String(seq).padStart(2, '0')}Z`, turn: coords.turn ?? null, step: coords.step ?? null };
}

describe('foldEvents — steps do not stack', () => {
  it('folds many tool calls of one step into ONE step node with one line per call', () => {
    seq = 0;
    const events = [
      ev('user', { content: 'write it' }, { turn: 1 }),
      ev('step_start', { turn: 1, step: 1, model: 'm' }, { turn: 1, step: 1 }),
      ev('step_end', { turn: 1, step: 1, cost_cents: 0.3, duration_ms: 1200, finish_reason: 'tool_calls' }, { turn: 1, step: 1 }),
      // the run path records tool calls AFTER step_end — they still belong to step 1
      ev('tool_call', { tool: 'ReadScene', iteration: 1, result: { ok: true } }),
      ev('tool_call', { tool: 'CreateShot', iteration: 1, result: { ok: true } }),
      ev('tool_call', { tool: 'CreateShot', iteration: 1, result: { ok: false, error: 'nope' } }),
      ev('step_start', { turn: 1, step: 2, model: 'm' }, { turn: 1, step: 2 }),
    ];
    const nodes = foldEvents(events, { isRunning: true });
    const steps = nodes.filter((n) => n.kind === 'step');
    expect(steps).toHaveLength(2);
    const s1 = steps[0];
    if (s1.kind !== 'step') throw new Error();
    expect(s1.lines.filter((l) => l.type === 'tool')).toHaveLength(3);
    expect(s1.summary.tools).toBe(3);
    expect(s1.lines[2].ok).toBe(false);
    expect(s1.live).toBe(false);
    expect(s1.summary.durationMs).toBe(1200);
    expect(s1.summary.costCents).toBe(0.3);
    // only the current step is live
    const live = liveStep(nodes);
    expect(live?.step).toBe(2);
    expect(steps.filter((s) => s.kind === 'step' && s.live)).toHaveLength(1);
  });

  it('a settled run has no live step even if the last step never ended', () => {
    seq = 0;
    const nodes = foldEvents([ev('step_start', { turn: 1, step: 1 }, { turn: 1, step: 1 })], { isRunning: false });
    expect(liveStep(nodes)).toBeNull();
  });

  it('retries and compactions update one line in place, assistant text after step_end attaches to that step', () => {
    seq = 0;
    const nodes = foldEvents([
      ev('step_start', { turn: 1, step: 1 }, { turn: 1, step: 1 }),
      ev('llm_retry', { attempt: 1, max_retries: 3, delay_ms: 1000 }),
      ev('llm_retry', { attempt: 2, max_retries: 3, delay_ms: 2000 }),
      ev('compaction_start', { tokens_before: 9000 }),
      ev('compaction_end', { tokens_before: 9000, tokens_after: 4000 }),
      ev('todo_write', { todos: [{ content: 'a', status: 'in_progress', active_form: 'Doing a' }], counts: { total: 4, completed: 1 } }),
      ev('step_end', { turn: 1, step: 1, cost_cents: 0.5 }, { turn: 1, step: 1 }),
      ev('assistant', { content: 'hello world' }),
      ev('turn_end', { reason: 'completed' }, { turn: 1 }),
    ]);
    const step = nodes.find((n) => n.kind === 'step');
    if (!step || step.kind !== 'step') throw new Error();
    expect(step.lines.map((l) => l.type)).toEqual(['retry', 'compaction', 'todo', 'output']);
    expect(step.lines[0].count).toBe(2);
    expect(step.summary.retries).toBe(2);
    expect(step.summary.compactions).toBe(1);
    expect(step.summary.todo).toEqual({ done: 1, total: 4 });
    expect(step.lines[2].label).toBe('Doing a');
    expect(step.summary.outputs).toBe(1);
    const end = nodes.find((n) => n.kind === 'turn_end');
    expect(end).toMatchObject({ kind: 'turn_end', reason: 'completed', costCents: 0.5, steps: 1 });
    expect(nodes.filter((n) => n.kind === 'step')).toHaveLength(1);
  });

  it('legacy rows without coordinates or step_start fold by tool iteration', () => {
    seq = 0;
    const nodes = foldEvents([
      ev('user', { content: 'q' }),
      ev('tool_call', { tool: 'A', iteration: 1 }),
      ev('tool_call', { tool: 'B', iteration: 1 }),
      ev('tool_call', { tool: 'C', iteration: 2 }),
      ev('assistant', { content: 'done' }),
    ], { isRunning: false });
    const steps = nodes.filter((n) => n.kind === 'step');
    expect(steps.map((s) => (s.kind === 'step' ? [s.step, s.lines.length] : null))).toEqual([[1, 2], [2, 2]]);
    expect(steps.every((s) => s.kind === 'step' && !s.live)).toBe(true);
  });

  it('renders inbox / budget / denied / error nodes and ignores unknown types', () => {
    seq = 0;
    const nodes = foldEvents([
      ev('inbox_claimed', { kind: 'steer', turn: 1, step: 4 }, { turn: 1, step: 4 }),
      ev('budget_check', { action: 'warn', pct: 81.2, spent_cents: 81.2, budget_cents: 100 }),
      ev('capability_denied', { tool: 'CreateShot', reason: 'write_level=none' }),
      ev('error', { kind: 'empty_response' }),
      ev('something_new_from_the_future', { x: 1 }),
      ev('system', {}),
    ]);
    expect(nodes.map((n) => n.kind)).toEqual(['inbox', 'budget', 'denied', 'error']);
    expect(nodes[0]).toMatchObject({ inboxKind: 'steer', step: 4 });
    expect(nodes[1]).toMatchObject({ action: 'warn', pct: 81.2 });
  });

  it('does not crash on garbage', () => {
    expect(foldEvents([] as AgentRunEvent[])).toEqual([]);
    expect(foldEvents([{ seq: 1, event_type: 'tool_call', payload: null as unknown as Record<string, unknown>, created_at: '' }])).toHaveLength(1);
    expect(foldEvents([null as unknown as AgentRunEvent])).toEqual([]);
  });
});


describe('foldEvents — real wire shape: nested payload fields are JSON strings', () => {
  it('reads usage / counts / todos / result through JSON.parse, as _truncate_payload writes them', () => {
    seq = 0;
    const nodes = foldEvents([
      ev('step_start', { turn: 1, step: 1, model: 'm' }, { turn: 1, step: 1 }),
      ev('todo_write', { todos: JSON.stringify([{ content: 'a', status: 'in_progress', active_form: 'Doing a' }]), counts: JSON.stringify({ total: 3, completed: 1, in_progress: 1 }) }),
      ev('step_end', { turn: 1, step: 1, usage: JSON.stringify({ prompt: 3067, completion: 629, cached: 0 }), cost_cents: null, duration_ms: 16903 }, { turn: 1, step: 1 }),
      ev('tool_call', { tool: 'CreateShot', iteration: 1, result: '{"ok": false, "error": "nope"}' }),
    ], { isRunning: false });
    const step = nodes.find((n) => n.kind === 'step');
    if (!step || step.kind !== 'step') throw new Error();
    expect(step.summary.todo).toEqual({ done: 1, total: 3 });
    expect(step.lines.find((l) => l.type === 'todo')?.label).toBe('Doing a');
    expect(step.lines.find((l) => l.type === 'tool')?.ok).toBe(false);
    expect(step.summary.durationMs).toBe(16903);
  });
});


describe('foldEvents — tool timeouts (harness 2b-1 §3)', () => {
  it('a timed-out tool result is a failed line with the timeout detail', () => {
    const nodes = foldEvents([
      ev('step_start', { turn: 1, step: 1, model: 'm' }, { turn: 1, step: 1 }),
      ev('tool_call', { tool: 'ResourceFetch', iteration: 1, result: { error: 'timeout', timed_out: true, timeout_s: 60, elapsed_s: 60.004, tool: 'ResourceFetch' } }),
      ev('tool_call', { tool: 'Skill', iteration: 1, result: { ok: false, error: 'nope' } }),
    ]);
    const step = nodes.find((n) => n.kind === 'step');
    expect(step?.kind).toBe('step');
    const [timed, failed] = (step as { lines: { ok: boolean; detail: Record<string, unknown> | null }[] }).lines;
    expect(timed.ok).toBe(false);
    expect(timed.detail).toMatchObject({ timedOut: true, timeoutS: 60, elapsedS: 60.004 });
    expect(failed.ok).toBe(false);
    expect(failed.detail?.timedOut).toBe(false);
  });
});
