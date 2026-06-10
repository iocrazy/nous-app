import { describe, it, expect } from 'vitest';
import { groupTasksByFlow, summarizeFlowItems, flowDisplayTitle } from './flowGrouping';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

let seq = 0;
function task(overrides: Partial<UnifiedTask>): UnifiedTask {
  seq += 1;
  return {
    id: `t${seq}`,
    user_id: 'u1',
    task_type: 'download',
    status: 'completed',
    title: `Task ${seq}`,
    progress: 100,
    metadata: {},
    created_at: `2026-06-10T10:00:${String(seq).padStart(2, '0')}Z`,
    ...overrides,
  } as UnifiedTask;
}

describe('groupTasksByFlow', () => {
  it('groups same-flow tasks into one flow item, steps ordered by created_at', () => {
    const parse = task({ task_type: 'parse', flow_id: 'f1' });
    const download = task({ task_type: 'download', flow_id: 'f1' });
    const audio = task({ task_type: 'extract_audio', flow_id: 'f1' });
    // shuffled input order
    const items = groupTasksByFlow([audio, parse, download]);

    expect(items).toHaveLength(1);
    const flow = items[0];
    expect(flow.kind).toBe('flow');
    if (flow.kind !== 'flow') return;
    expect(flow.flowId).toBe('f1');
    expect(flow.steps.map((s) => s.task_type)).toEqual(['parse', 'download', 'extract_audio']);
  });

  it('tasks without flow_id stay standalone singles', () => {
    const single = task({ flow_id: undefined });
    const items = groupTasksByFlow([single]);
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('single');
  });

  it('current step = first processing, else first pending', () => {
    const parse = task({ task_type: 'parse', flow_id: 'f1', status: 'completed' });
    const download = task({ task_type: 'download', flow_id: 'f1', status: 'processing', progress: 42 });
    const audio = task({ task_type: 'extract_audio', flow_id: 'f1', status: 'pending' });
    const items = groupTasksByFlow([parse, download, audio]);
    const flow = items[0];
    if (flow.kind !== 'flow') throw new Error('expected flow');
    expect(flow.current?.task_type).toBe('download');
    expect(flow.hasActive).toBe(true);
  });

  it('flow status: failed wins over done; all-terminal w/o failure = done', () => {
    const ok = groupTasksByFlow([
      task({ flow_id: 'f1', status: 'completed' }),
      task({ flow_id: 'f1', status: 'completed' }),
    ])[0];
    if (ok.kind !== 'flow') throw new Error('expected flow');
    expect(ok.hasActive).toBe(false);
    expect(ok.failedCount).toBe(0);

    const bad = groupTasksByFlow([
      task({ flow_id: 'f2', status: 'completed' }),
      task({ flow_id: 'f2', status: 'failed' }),
    ])[0];
    if (bad.kind !== 'flow') throw new Error('expected flow');
    expect(bad.failedCount).toBe(1);
  });

  it('sorts items by newest child desc (flows and singles interleaved)', () => {
    const oldFlow = [
      task({ flow_id: 'f-old', created_at: '2026-06-10T08:00:00Z' }),
      task({ flow_id: 'f-old', created_at: '2026-06-10T08:01:00Z' }),
    ];
    const newer = task({ flow_id: undefined, created_at: '2026-06-10T09:00:00Z' });
    const newestFlow = [
      task({ flow_id: 'f-new', created_at: '2026-06-10T10:30:00Z' }),
    ];
    const items = groupTasksByFlow([...oldFlow, newer, ...newestFlow]);
    expect(items.map((i) => (i.kind === 'flow' ? i.flowId : 'single'))).toEqual([
      'f-new',
      'single',
      'f-old',
    ]);
  });
});

describe('flowDisplayTitle', () => {
  it('prefers the download title stripped of its prefix', () => {
    const items = groupTasksByFlow([
      task({ task_type: 'parse', flow_id: 'f1', title: 'Parse https://v.douyin.com/xyz' }),
      task({ task_type: 'download', flow_id: 'f1', title: 'Download Cute Bunny Vlog' }),
    ]);
    const flow = items[0];
    if (flow.kind !== 'flow') throw new Error('expected flow');
    expect(flowDisplayTitle(flow)).toBe('Cute Bunny Vlog');
  });

  it('falls back to the parse subtitle (video title backfill) before raw parse title', () => {
    const items = groupTasksByFlow([
      task({
        task_type: 'parse',
        flow_id: 'f1',
        title: 'Parse https://v.douyin.com/xyz',
        subtitle: 'Cute Bunny Vlog',
      }),
    ]);
    const flow = items[0];
    if (flow.kind !== 'flow') throw new Error('expected flow');
    expect(flowDisplayTitle(flow)).toBe('Cute Bunny Vlog');
  });
});

describe('summarizeFlowItems', () => {
  it('counts flows (not subtasks) by user-facing semantics', () => {
    const items = groupTasksByFlow([
      // flow A: mid-pipeline (processing) → running
      task({ flow_id: 'a', status: 'completed' }),
      task({ flow_id: 'a', status: 'processing' }),
      // flow B: all queued → queued
      task({ flow_id: 'b', status: 'pending' }),
      // flow C: all done → completed
      task({ flow_id: 'c', status: 'completed' }),
      task({ flow_id: 'c', status: 'completed' }),
      // flow D: one failure → failed
      task({ flow_id: 'd', status: 'completed' }),
      task({ flow_id: 'd', status: 'failed' }),
      // standalone single → counted as its own unit
      task({ flow_id: undefined, status: 'completed' }),
    ]);
    const counts = summarizeFlowItems(items);
    expect(counts).toEqual({ running: 1, queued: 1, completed: 2, failed: 1 });
  });
});
