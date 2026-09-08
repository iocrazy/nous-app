/**
 * A1 —— 「等我的」三类注意力项聚合。
 *
 * 三个来源本来散在三处：agent 提问在 Task Center 的 feed 里、审批在 TopBar
 * 的 ApprovalsPanel 里、待验收只是列表里一行普通 issue。聚到 Issues 页顶部
 * 一条横条上，回答「现在有什么卡在我手上」。
 */

import { describe, it, expect } from 'vitest';
import type { NeedsInputItem } from '../../services/issuesService';
import type { AILibraryApprovalRequest } from '../../types';
import type { UiIssue } from './types';
import { buildAttentionItems } from './attentionItems';

const needsInput: NeedsInputItem = {
  issue_id: '1001',
  title: 'Confirm publish schedule',
  question: 'Monday or Wednesday?',
  project_id: null,
  team_id: '2002',
  asked_at: '2026-08-03T00:00:00Z',
  assignee_agent_id: 'a1',
  identifier: 'MH-1001',
};

const approval: AILibraryApprovalRequest = {
  id: 'ap-1',
  agent_id: 'a1',
  session_id: null,
  run_id: null,
  hook_name: 'pre_tool_use',
  reason: 'Wants to publish to the live account',
  payload: {},
  created_at: '2026-08-03T00:00:00Z',
  expires_at: null,
};

const inReview = {
  id: 3003,
  identifier: 'NOUS-7',
  title: 'Trailer cut v2',
  status: 'in_review',
} as unknown as UiIssue;

describe('buildAttentionItems', () => {
  it('aggregates one of each source, typed and titled', () => {
    const items = buildAttentionItems([needsInput], [approval], [inReview]);
    expect(items).toHaveLength(3);
    expect(items.map((i) => i.type)).toEqual(['question', 'approval', 'review']);

    const [q, a, r] = items;
    expect(q.title).toBe('Confirm publish schedule');
    expect(q.detail).toBe('Monday or Wednesday?');
    expect(q.issueId).toBe(1001);

    expect(a.id).toBe('ap-1');
    expect(a.detail).toBe('Wants to publish to the live account');

    expect(r.title).toBe('Trailer cut v2');
    expect(r.issueId).toBe(3003);
  });

  it('returns an empty list when nothing is waiting', () => {
    expect(buildAttentionItems([], [], [])).toEqual([]);
  });

  it('tolerates a question with no stated reason', () => {
    const items = buildAttentionItems([{ ...needsInput, question: null }], [], []);
    expect(items[0].detail).toBeNull();
  });

  it('gives every item a unique id so React keys never collide', () => {
    const items = buildAttentionItems(
      [needsInput, { ...needsInput, issue_id: '1002' }],
      [approval],
      [inReview],
    );
    expect(new Set(items.map((i) => i.id)).size).toBe(items.length);
  });
});

describe('buildAttentionItems — 上游畸形数据不得掀翻整页', () => {
  it('treats a nullish feed as "nothing waiting"', () => {
    // Real incident: TaskManagerContext did `setNeedsInputItems(res.items)`
    // with no fallback, so a 200 whose body lacked `items` parked `undefined`
    // in state and this strip took the whole Issues page down.
    expect(buildAttentionItems(undefined, undefined, undefined)).toEqual([]);
    expect(buildAttentionItems(null, null, null)).toEqual([]);
    expect(buildAttentionItems(undefined, [approval], undefined)).toHaveLength(1);
  });
});


describe('buildAttentionItems — paused issues (harness P4)', () => {
  it('lists a paused issue as its own kind, linking to the issue, after the other three', () => {
    const paused = { id: 4004, identifier: 'NOUS-8', title: 'Paused draft', status: 'in_progress', raw: { paused_at: '2026-09-05T00:00:00Z' } } as unknown as UiIssue;
    const items = buildAttentionItems([needsInput], [approval], [inReview], [paused]);
    expect(items.map((i) => i.type)).toEqual(['question', 'approval', 'review', 'paused']);
    expect(items[3]).toMatchObject({ id: 'paused:4004', issueId: 4004, title: 'Paused draft', detail: 'NOUS-8' });
    // the fourth source is optional — older callers keep working
    expect(buildAttentionItems([], [], [])).toEqual([]);
  });
});

// Phase 2a §4: a paused issue is a `paused` card; a paused issue WITH queued
// comments becomes a `queued` card (resume is what runs them), never both.
describe('buildAttentionItems — paused vs queued', () => {
  const paused = {
    id: 5005,
    identifier: 'NOUS-9',
    title: 'Paused with mail',
    status: 'in_progress',
    raw: { paused_at: '2026-09-08T00:00:00Z' },
  } as unknown as UiIssue;

  it('is a paused card when nothing is queued', () => {
    const items = buildAttentionItems([], [], [], [paused]);
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe('paused');
    expect(items[0].issueId).toBe(5005);
  });

  it('turns into a queued card carrying the count when the inbox has mail', () => {
    const items = buildAttentionItems([], [], [], [paused], { '5005': { count: 2, oldestAt: '2026-09-08T00:00:00Z' } });
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ type: 'queued', id: 'queued:5005', detail: '2 queued', issueId: 5005 });
  });

  it('ignores summary rows for issues that are not paused', () => {
    const items = buildAttentionItems([], [], [], [], { '5005': { count: 2, oldestAt: '2026-09-08T00:00:00Z' } });
    expect(items).toHaveLength(0);
  });
});
