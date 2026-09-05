import { describe, expect, it } from 'vitest';

import type { UnifiedTask } from '../contexts/TaskManagerContext';
import { groupTasks, targetGroup } from './taskDisplay';

const task = (id: string, over: Partial<UnifiedTask> = {}): UnifiedTask =>
  ({ id, user_id: 'u', task_type: 'agent', status: 'completed', title: id, progress: 100, metadata: {}, created_at: '2026-09-05T00:00:00Z', ...over }) as UnifiedTask;

describe('groupTasks(target) — history folds by what the run was FOR', () => {
  it('issue beats conversation beats flow beats batch beats standalone', () => {
    expect(targetGroup(task('a', { metadata: { agent_issue_id: '48', agent_conversation_id: '9' } })).key).toBe('issue:48');
    expect(targetGroup(task('b', { metadata: { agent_conversation_id: '3107' } }))).toEqual({ key: 'conversation:3107', label: 'Conversation 3107' });
    expect(targetGroup(task('c', { flow_id: 'flow-abc-123' })).key).toBe('flow:flow-abc-123');
    expect(targetGroup(task('d', { metadata: { batch_id: 'batch-xyz' } })).key).toBe('batch:batch-xyz');
    expect(targetGroup(task('e')).key).toBe('__standalone__');
  });

  it('two runs on one issue fold into one group; DBOS tasks and agent runs share the same shape', () => {
    const groups = groupTasks(
      [
        task('r1', { metadata: { agent_issue_id: '48' } }),
        task('r2', { metadata: { agent_issue_id: '48' } }),
        task('t1', { task_type: 'ai_summary', metadata: { issue_id: '48' } }),
        task('r3', { metadata: { agent_conversation_id: '9' } }),
      ],
      'target',
    );
    expect(groups.map((g) => [g.key, g.tasks.length])).toEqual([
      ['conversation:9', 1],
      ['issue:48', 3],
    ]);
  });
});
