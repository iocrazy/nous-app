/**
 * A1 —— Realtime UPDATE 整行替换导致 running chip 闪灭。
 *
 * `issues` 的 Realtime 发布白名单（mig 172）**排除** execution_state /
 * execution_locked_at / dbos_workflow_id。整行替换会把这三列写成 undefined，
 * 于是每来一个无关的 UPDATE（比如 updated_at 变了），列表行的 running chip
 * 就闪一下没了 —— 直到下一次 REST 拉取才回来。
 */

import { describe, it, expect } from 'vitest';
import type { Issue } from '../../services/issuesService';
import { mergeRealtimeIssue } from './mergeRealtimeIssue';
import { toUiIssue } from './uiIssue';

const AGENTS = {};

function rawIssue(over: Partial<Issue> = {}): Issue {
  return {
    id: 1,
    identifier: 'NOUS-1',
    title: 'Draft the pilot',
    description: null,
    status: 'in_progress',
    priority: 'medium',
    project_id: null,
    parent_id: null,
    assignee_user_id: null,
    assignee_agent_id: null,
    dbos_workflow_id: 'wf-1',
    execution_state: { turn: 2, agent_outcome: null },
    execution_locked_at: '2026-08-03T00:00:00Z',
    origin_kind: 'manual',
    started_at: '2026-08-03T00:00:00Z',
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
    ...over,
  } as unknown as Issue;
}

/** What Realtime actually delivers: the whitelisted columns only. */
function realtimePayload(over: Partial<Issue> = {}): Issue {
  const full = rawIssue(over) as unknown as Record<string, unknown>;
  const { dbos_workflow_id: _w, execution_state: _s, execution_locked_at: _l, ...rest } = full;
  return rest as unknown as Issue;
}

describe('mergeRealtimeIssue', () => {
  it('keeps the publication-excluded fields from the previous row', () => {
    const prev = toUiIssue(rawIssue(), AGENTS);
    const merged = mergeRealtimeIssue(prev, realtimePayload({ updated_at: '2026-08-03T01:00:00Z' }), AGENTS);
    expect(merged.raw.dbos_workflow_id).toBe('wf-1');
    expect(merged.raw.execution_state).toEqual({ turn: 2, agent_outcome: null });
    expect(merged.raw.execution_locked_at).toBe('2026-08-03T00:00:00Z');
  });

  it('takes whitelisted fields from the incoming row', () => {
    const prev = toUiIssue(rawIssue(), AGENTS);
    const merged = mergeRealtimeIssue(
      prev,
      realtimePayload({ status: 'done', title: 'Draft the pilot (final)' }),
      AGENTS,
    );
    expect(merged.status).toBe('done');
    expect(merged.title).toBe('Draft the pilot (final)');
    expect(merged.raw.status).toBe('done');
  });

  it('prefers an incoming excluded field when the payload does carry one', () => {
    // Belt and braces: if the publication is ever widened, fresh data wins.
    const prev = toUiIssue(rawIssue(), AGENTS);
    const merged = mergeRealtimeIssue(prev, rawIssue({ dbos_workflow_id: 'wf-2' }), AGENTS);
    expect(merged.raw.dbos_workflow_id).toBe('wf-2');
  });

  it('survives a previous row with no raw payload', () => {
    const prev = { id: 1 } as never;
    const merged = mergeRealtimeIssue(prev, realtimePayload(), AGENTS);
    expect(merged.id).toBe(1);
    expect(merged.raw.dbos_workflow_id).toBeUndefined();
  });
});
