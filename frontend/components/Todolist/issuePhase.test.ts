import { describe, expect, it } from 'vitest';

import { PHASE_ORDER, QUICK_PHASES, issuePhase } from './issuePhase';
import type { UiIssue } from './types';

function issue(status: string, raw: Record<string, unknown> = {}): UiIssue {
  return { id: 1, identifier: 'X-1', title: 't', status, raw } as unknown as UiIssue;
}

describe('issuePhase — same priority as the server rollup', () => {
  it('paused beats everything', () => {
    expect(issuePhase(issue('in_progress', { paused_at: '2026-09-05T00:00:00Z', dbos_workflow_id: 'wf', execution_state: { awaiting_input: {} } }))).toBe('paused');
  });
  it('waiting_input beats running, from either marker', () => {
    expect(issuePhase(issue('needs_followup', { dbos_workflow_id: 'wf', execution_state: { agent_outcome: 'needs_input' } }))).toBe('waiting_input');
    expect(issuePhase(issue('in_progress', { dbos_workflow_id: 'wf', execution_state: { awaiting_input: { question: 'q' } } }))).toBe('waiting_input');
    // an empty_output stall at the same status is NOT a question
    expect(issuePhase(issue('needs_followup', { execution_state: { agent_outcome: 'empty_output' } }))).toBe('idle');
  });
  it('running needs a live workflow on a non-terminal status', () => {
    expect(issuePhase(issue('in_progress', { dbos_workflow_id: 'wf' }))).toBe('running');
    expect(issuePhase(issue('done', { dbos_workflow_id: 'wf' }))).toBe('done');
    expect(issuePhase(issue('in_progress', {}))).toBe('idle');
  });
  it('blocked, done, idle', () => {
    expect(issuePhase(issue('blocked'))).toBe('blocked');
    expect(issuePhase(issue('cancelled'))).toBe('done');
    expect(issuePhase(issue('todo'))).toBe('idle');
    expect(issuePhase(issue('backlog'))).toBe('idle');
  });
  it('group order puts what needs a person first and done last; quick chips are the four actionable ones', () => {
    expect(PHASE_ORDER[0]).toBe('waiting_input');
    expect(PHASE_ORDER[PHASE_ORDER.length - 1]).toBe('done');
    expect(QUICK_PHASES).toEqual(['waiting_input', 'running', 'paused', 'blocked']);
  });
});
