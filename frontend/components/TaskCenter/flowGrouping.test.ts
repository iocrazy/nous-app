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
    expect(counts).toEqual({ running: 1, queued: 1, completed: 2, failed: 1, waiting: 0 });
  });
});

describe('retry collapsing — one step per (task_type, resource) subject', () => {
  // The shape from the production report (flow 3b0918de): one transcription
  // plus THREE ai_summary rows — endpoint-level retries each created a new
  // task_tracking row that _find_joinable_flow_id joined into the same flow.
  const incidentRows = () => [
    task({
      task_type: 'ai_transcription', flow_id: 'f-incident', resource_id: 'r1',
      status: 'completed', created_at: '2026-08-14T10:00:00Z',
    }),
    task({
      task_type: 'ai_summary', flow_id: 'f-incident', resource_id: 'r1',
      status: 'failed', created_at: '2026-08-14T10:01:00Z',
    }),
    task({
      task_type: 'ai_summary', flow_id: 'f-incident', resource_id: 'r1',
      status: 'failed', created_at: '2026-08-14T10:02:00Z',
    }),
    task({
      task_type: 'ai_summary', flow_id: 'f-incident', resource_id: 'r1',
      status: 'completed', created_at: '2026-08-14T10:03:00Z',
    }),
  ];

  function incidentFlow() {
    const flow = groupTasksByFlow(incidentRows())[0];
    if (flow.kind !== 'flow') throw new Error('expected flow');
    return flow;
  }

  it('collapses the three summary attempts into ONE step carrying the newest status', () => {
    const flow = incidentFlow();
    expect(flow.steps.map((s) => s.task_type)).toEqual(['ai_transcription', 'ai_summary']);
    const summary = flow.steps[1];
    // Newest attempt succeeded → the step is done, not failed.
    expect(summary.status).toBe('completed');
    expect(summary.created_at).toBe('2026-08-14T10:03:00Z');
    expect(flow.attemptCounts[summary.id]).toBe(3);
    expect(flow.failedCount).toBe(0);
    expect(flow.doneCount).toBe(2);
  });

  it('reports the whole flow as completed once the last attempt succeeded', () => {
    const counts = summarizeFlowItems(groupTasksByFlow(incidentRows()));
    expect(counts).toEqual({ running: 0, queued: 0, completed: 1, failed: 0, waiting: 0 });
  });

  it('is not a happy-path liar: newest attempt failed → the step is failed', () => {
    const flow = groupTasksByFlow([
      task({
        task_type: 'ai_summary', flow_id: 'f2', resource_id: 'r1',
        status: 'completed', created_at: '2026-08-14T10:00:00Z',
      }),
      task({
        task_type: 'ai_summary', flow_id: 'f2', resource_id: 'r1',
        status: 'failed', created_at: '2026-08-14T10:05:00Z',
      }),
    ])[0];
    if (flow.kind !== 'flow') throw new Error('expected flow');
    expect(flow.steps).toHaveLength(1);
    expect(flow.steps[0].status).toBe('failed');
    expect(flow.failedCount).toBe(1);
    expect(flow.doneCount).toBe(0);
  });

  it('keeps batch fan-out intact: same task_type, different resources are NOT retries', () => {
    const rows = Array.from({ length: 5 }, (_, i) =>
      task({
        task_type: 'download', flow_id: 'f-batch', resource_id: `r${i}`,
        status: 'completed', created_at: `2026-08-14T10:0${i}:00Z`,
      }),
    );
    const flow = groupTasksByFlow(rows)[0];
    if (flow.kind !== 'flow') throw new Error('expected flow');
    expect(flow.steps).toHaveLength(5);
    expect(flow.doneCount).toBe(5);
    expect(Object.values(flow.attemptCounts).every((n) => n === 1)).toBe(true);
  });

  it('rows without a resource/media subject (parse root) never merge', () => {
    const flow = groupTasksByFlow([
      task({ task_type: 'parse', flow_id: 'f3', status: 'failed', created_at: '2026-08-14T10:00:00Z' }),
      task({ task_type: 'parse', flow_id: 'f3', status: 'completed', created_at: '2026-08-14T10:01:00Z' }),
    ])[0];
    if (flow.kind !== 'flow') throw new Error('expected flow');
    expect(flow.steps).toHaveLength(2);
    expect(flow.failedCount).toBe(1);
  });

  it('falls back to media_id when the row has no resource_id yet', () => {
    const flow = groupTasksByFlow([
      task({
        task_type: 'ai_summary', flow_id: 'f4', media_id: 'm1',
        status: 'failed', created_at: '2026-08-14T10:00:00Z',
      }),
      task({
        task_type: 'ai_summary', flow_id: 'f4', media_id: 'm1',
        status: 'completed', created_at: '2026-08-14T10:01:00Z',
      }),
    ])[0];
    if (flow.kind !== 'flow') throw new Error('expected flow');
    expect(flow.steps).toHaveLength(1);
    expect(flow.steps[0].status).toBe('completed');
  });

  it('keeps steps in dispatch order (first attempt), and current = newest attempt in flight', () => {
    const flow = groupTasksByFlow([
      task({
        task_type: 'ai_summary', flow_id: 'f5', resource_id: 'r1',
        status: 'failed', created_at: '2026-08-14T10:00:00Z',
      }),
      task({
        task_type: 'ai_transcription', flow_id: 'f5', resource_id: 'r1',
        status: 'completed', created_at: '2026-08-14T10:01:00Z',
      }),
      task({
        task_type: 'ai_summary', flow_id: 'f5', resource_id: 'r1',
        status: 'processing', created_at: '2026-08-14T10:02:00Z',
      }),
    ])[0];
    if (flow.kind !== 'flow') throw new Error('expected flow');
    expect(flow.steps.map((s) => s.task_type)).toEqual(['ai_summary', 'ai_transcription']);
    expect(flow.current?.task_type).toBe('ai_summary');
    expect(flow.current?.status).toBe('processing');
    expect(flow.hasActive).toBe(true);
    expect(flow.latestCreatedAt).toBe('2026-08-14T10:02:00Z');
  });
});

import { countActiveFlowUnits } from './flowGrouping';

// The TopBar badge said "2" for one "Generate 2 images" submission while the
// panel header right under it said "1 queued": the badge counted task rows
// (server active total) and the header counted flows. One number, flow units.
describe('countActiveFlowUnits', () => {
  const t = (o: Partial<UnifiedTask>): UnifiedTask =>
    ({ id: Math.random().toString(36).slice(2), user_id: 'u', task_type: 'canvas_gen', title: 'Generate image',
       status: 'pending', progress: 0, metadata: {}, created_at: '2026-09-05T00:00:00Z', ...o }) as UnifiedTask;

  it('counts one flow of N active siblings as 1', () => {
    expect(countActiveFlowUnits([
      t({ flow_id: 'f', status: 'processing' }), t({ flow_id: 'f', status: 'pending' }),
    ])).toBe(1);
  });

  it('counts ungrouped active tasks one each', () => {
    expect(countActiveFlowUnits([t({ status: 'processing' }), t({ status: 'pending' })])).toBe(2);
  });

  it('ignores finished rows and in-flight uploads (UploadContext draws those)', () => {
    expect(countActiveFlowUnits([
      t({ status: 'completed' }), t({ status: 'failed' }),
      t({ task_type: 'upload', status: 'processing' }),
    ])).toBe(0);
  });

  it('a flow whose steps are all finished is not active', () => {
    expect(countActiveFlowUnits([t({ flow_id: 'f', status: 'completed' }), t({ flow_id: 'f', status: 'failed' })])).toBe(0);
  });
});
