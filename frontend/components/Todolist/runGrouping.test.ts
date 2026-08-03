/**
 * A2 —— 运行组折叠：连续多轮 dispatch 折成一张卡，不再淹没对话。
 *
 * 分组边界的语义：comment / deliverable 打断（人一开口就是新一段），
 * system_status 不打断（同一次运行内部的状态翻转属于运行细节）。
 */

import { describe, it, expect } from 'vitest';
import type { IssueMessage } from '../../services/issueMessageService';
import { groupAgentRuns } from './runGrouping';

let seq = 0;
function run(over: Partial<IssueMessage> & { meta?: Record<string, unknown> } = {}): IssueMessage {
  seq += 1;
  return {
    id: `r-${seq}`,
    issue_id: 1,
    kind: 'agent_run',
    author_agent_id: 'a1',
    author_user_id: null,
    body: 'did a thing',
    created_at: `2026-08-03T00:0${seq}:00Z`,
    duration_seconds: 30,
    meta: { status: 'completed', prompt_tokens: 1000, completion_tokens: 200, cost_cents: 5 },
    ...over,
  } as unknown as IssueMessage;
}

function comment(): IssueMessage {
  seq += 1;
  return {
    id: `c-${seq}`,
    issue_id: 1,
    kind: 'comment',
    author_user_id: 'u1',
    author_agent_id: null,
    body: 'looks good',
    created_at: `2026-08-03T00:0${seq}:00Z`,
    meta: {},
  } as unknown as IssueMessage;
}

function status(): IssueMessage {
  seq += 1;
  return {
    id: `s-${seq}`,
    issue_id: 1,
    kind: 'system_status',
    author_user_id: null,
    author_agent_id: null,
    from_status: 'todo',
    to_status: 'in_progress',
    created_at: `2026-08-03T00:0${seq}:00Z`,
    meta: {},
  } as unknown as IssueMessage;
}

describe('groupAgentRuns', () => {
  it('folds three consecutive agent runs into one group with summed totals', () => {
    const msgs = [run(), run(), run()];
    const out = groupAgentRuns(msgs);
    expect(out).toHaveLength(1);
    const g = out[0];
    if (g.kind !== 'run_group') throw new Error('expected a run_group');
    expect(g.runs).toHaveLength(3);
    expect(g.totals.tokens).toBe(3 * 1200);
    expect(g.totals.costCents).toBe(15);
    expect(g.totals.durationSeconds).toBe(90);
    expect(g.startedAt).toBe(msgs[0].created_at);
    expect(g.anyRunning).toBe(false);
  });

  it('does not group across a comment', () => {
    const out = groupAgentRuns([run(), comment(), run()]);
    expect(out.map((e) => e.kind)).toEqual(['single', 'single', 'single']);
    expect(out.map((e) => (e.kind === 'single' ? e.message.kind : null)))
      .toEqual(['agent_run', 'comment', 'agent_run']);
  });

  it('keeps runs in the same group when a status event sits between them', () => {
    const msgs = [run(), status(), run()];
    const out = groupAgentRuns(msgs);
    expect(out).toHaveLength(1);
    const g = out[0];
    if (g.kind !== 'run_group') throw new Error('expected a run_group');
    expect(g.runs).toHaveLength(2);
    // The swallowed status row is preserved for the expanded view.
    expect(g.items).toHaveLength(3);
  });

  it('leaves a lone agent run as a single, and does not swallow the status after it', () => {
    const out = groupAgentRuns([run(), status(), comment()]);
    expect(out.map((e) => e.kind)).toEqual(['single', 'single', 'single']);
    expect(out.map((e) => (e.kind === 'single' ? e.message.kind : null)))
      .toEqual(['agent_run', 'system_status', 'comment']);
  });

  it('flags a group as running when any member is still in flight', () => {
    const out = groupAgentRuns([
      run(),
      run({ meta: { status: 'running' } }),
    ]);
    const g = out[0];
    if (g.kind !== 'run_group') throw new Error('expected a run_group');
    expect(g.anyRunning).toBe(true);
  });

  it('defends against missing / string-typed meta numbers', () => {
    const out = groupAgentRuns([
      run({ meta: {}, duration_seconds: null }),
      run({ meta: { prompt_tokens: '500', completion_tokens: null, cost_cents: '3' }, duration_seconds: null }),
    ]);
    const g = out[0];
    if (g.kind !== 'run_group') throw new Error('expected a run_group');
    expect(g.totals.tokens).toBe(500);
    expect(g.totals.costCents).toBe(3);
    expect(g.totals.durationSeconds).toBe(0);
  });

  it('passes non-run messages through untouched', () => {
    const c = comment();
    const out = groupAgentRuns([c]);
    expect(out).toEqual([{ kind: 'single', key: c.id, message: c }]);
  });
});
