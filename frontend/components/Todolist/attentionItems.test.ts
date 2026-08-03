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
