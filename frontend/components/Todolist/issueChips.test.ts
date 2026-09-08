/**
 * A1 —— 列表/看板行尾两个 chip 的内容推导。
 *
 * running chip 加轮次/耗时后缀（「在跑」→「在跑第几轮、跑了多久」）；
 * needs_followup + agent_outcome==='needs_input' 时多一个「等你回复」chip，
 * tooltip 是 agent 的提问原文。
 */

import { describe, it, expect } from 'vitest';
import type { Issue } from '../../services/issuesService';
import type { UiIssue } from './types';
import { runningChipLabel, needsReplyChip, queuedChip } from './issueChips';

const NOW = new Date('2026-08-03T00:10:00Z');

function issue(over: Record<string, unknown> = {}, status = 'in_progress'): UiIssue {
  return {
    id: 1,
    identifier: 'NOUS-1',
    title: 'x',
    status,
    priority: 'medium',
    raw: {
      status,
      dbos_workflow_id: 'wf-1',
      started_at: null,
      execution_state: null,
      ...over,
    } as unknown as Issue,
  } as unknown as UiIssue;
}

describe('runningChipLabel', () => {
  it('appends the turn number and elapsed time when both are known', () => {
    const label = runningChipLabel(
      issue({ execution_state: { turn: 2 }, started_at: '2026-08-03T00:06:00Z' }),
      NOW,
    );
    expect(label).toBe('running · turn 2 · 4m 0s');
  });

  it('falls back to a bare label when there is no turn or start time', () => {
    expect(runningChipLabel(issue(), NOW)).toBe('running');
  });

  it('includes only the turn when the issue never recorded a start', () => {
    expect(runningChipLabel(issue({ execution_state: { turn: 5 } }), NOW))
      .toBe('running · turn 5');
  });

  it('returns null for a finished issue even if the workflow id lingers', () => {
    expect(runningChipLabel(issue({}, 'done'), NOW)).toBeNull();
    expect(runningChipLabel(issue({}, 'cancelled'), NOW)).toBeNull();
  });

  it('returns null once the lifecycle moved the issue past in_progress', () => {
    // in_review / needs_followup keep the dispatch id but the turn is over —
    // reading them as live is what showed `running · 860h` on month-old rows.
    expect(runningChipLabel(issue({ started_at: '2026-08-01T00:00:00Z' }, 'in_review'), NOW)).toBeNull();
    expect(runningChipLabel(issue({}, 'needs_followup'), NOW)).toBeNull();
  });

  it('returns null when no workflow ever ran', () => {
    expect(runningChipLabel(issue({ dbos_workflow_id: null }), NOW)).toBeNull();
  });
});

describe('needsReplyChip', () => {
  it('carries the agent question when the agent asked for input', () => {
    const chip = needsReplyChip(issue(
      { execution_state: { agent_outcome: 'needs_input', outcome_reason: 'Monday or Wednesday?' } },
      'needs_followup',
    ));
    expect(chip).toEqual({ question: 'Monday or Wednesday?' });
  });

  it('still fires when the agent asked without stating a reason', () => {
    const chip = needsReplyChip(issue(
      { execution_state: { agent_outcome: 'needs_input' } },
      'needs_followup',
    ));
    expect(chip).toEqual({ question: null });
  });

  it('does not fire for an empty_output stall parked at the same status', () => {
    expect(needsReplyChip(issue(
      { execution_state: { agent_outcome: 'empty_output', outcome_reason: 'no output' } },
      'needs_followup',
    ))).toBeNull();
  });

  it('does not fire for needs_followup with no execution state at all', () => {
    expect(needsReplyChip(issue({}, 'needs_followup'))).toBeNull();
  });

  it('does not fire for any other status', () => {
    expect(needsReplyChip(issue(
      { execution_state: { agent_outcome: 'needs_input' } },
      'in_progress',
    ))).toBeNull();
  });
});

// Phase 2a §4: the queued chip is a count of comments waiting in the inbox.
// Zero is silence, not "0 queued" — a chip that is always there is a chip
// nobody reads.
describe('queuedChip', () => {
  it('is null for zero, undefined and null', () => {
    expect(queuedChip(0)).toBeNull();
    expect(queuedChip(undefined)).toBeNull();
    expect(queuedChip(null)).toBeNull();
  });

  it('counts when something is waiting', () => {
    expect(queuedChip(1)).toBe('1 queued');
    expect(queuedChip(2)).toBe('2 queued');
  });
});
