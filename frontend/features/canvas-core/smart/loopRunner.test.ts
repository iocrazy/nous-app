// features/canvas-core/smart/loopRunner.test.ts
// From-loop cascade orchestration (Infinite-Canvas parity Phase 1 G3b):
// rounds × rotating prompts × counter injection, serial await-per-round vs
// parallel worker pool (limit 6, Infinite runSmartCascadeRoundsWithLimit),
// cooperative stop, fail-fast. Pure — caller/handlers injected.

import { describe, expect, it, vi } from 'vitest';
import { runLoopCascade } from './loopRunner';
import type { RunnerContext, RunnerResult } from './runner';
import type { CanvasNode } from '../types';

function loopNode(data: Record<string, unknown>): CanvasNode {
  return { id: 'loop1', type: 'loop', position: { x: 0, y: 0 }, data: { mode: 'serial', label: '', rounds: 1, round_start: 1, prompts: [''], ...data } };
}
function promptNode(id: string, body: string): CanvasNode {
  return { id, type: 'prompt', position: { x: 0, y: 0 }, data: { body, provider_slug: 'default', agent_id: null, run_status: 'idle', resource_refs: [] } };
}
const edge = (source: string, target: string) => ({ id: `e-${source}-${target}`, source, target, sourceHandle: null, targetHandle: null });

const okCaller = vi.fn(async (ctx: RunnerContext): Promise<RunnerResult> => ({ ok: true, text: `out(${ctx.body})`, error: null }));
const silentHandlers = { onStatusChange: () => {} };

describe('runLoopCascade — serial', () => {
  it('runs each round sequentially with the counter injected', async () => {
    const calls: string[] = [];
    const caller = async (ctx: RunnerContext): Promise<RunnerResult> => {
      calls.push(ctx.body);
      return { ok: true, text: '', error: null };
    };
    const summary = await runLoopCascade({
      loopId: 'loop1',
      nodes: [loopNode({ rounds: 3 }), promptNode('p1', '第《计数》张 / 共《总数》')],
      connections: [edge('loop1', 'p1')],
      caller,
      handlers: silentHandlers,
    });
    expect(calls).toEqual(['第1张 / 共3', '第2张 / 共3', '第3张 / 共3']);
    expect(summary).toMatchObject({ rounds: 3, completedRounds: 3, stopped: false, failed: false });
  });

  it('prepends the rotating loop prompt for each round', async () => {
    const calls: string[] = [];
    const caller = async (ctx: RunnerContext): Promise<RunnerResult> => {
      calls.push(ctx.body);
      return { ok: true, text: '', error: null };
    };
    await runLoopCascade({
      loopId: 'loop1',
      nodes: [loopNode({ rounds: 3, prompts: ['风格A', '风格B'] }), promptNode('p1', 'body')],
      connections: [edge('loop1', 'p1')],
      caller,
      handlers: silentHandlers,
    });
    expect(calls).toEqual(['风格A\n\nbody', '风格B\n\nbody', '风格A\n\nbody']);
  });

  it('runs only prompts downstream of the loop, in topo order', async () => {
    const calls: string[] = [];
    const caller = async (ctx: RunnerContext): Promise<RunnerResult> => {
      calls.push(ctx.promptId);
      return { ok: true, text: '', error: null };
    };
    await runLoopCascade({
      loopId: 'loop1',
      nodes: [
        loopNode({}),
        promptNode('p2', 'second'),
        promptNode('p1', 'first'),
        promptNode('p-detached', 'unrelated'),
      ],
      connections: [edge('loop1', 'p1'), edge('p1', 'p2')],
      caller,
      handlers: silentHandlers,
    });
    expect(calls).toEqual(['p1', 'p2']);
  });

  it('a failed round aborts the remaining rounds (matches Infinite serial)', async () => {
    let round = 0;
    const caller = async (): Promise<RunnerResult> => {
      round += 1;
      return round === 1 ? { ok: false, text: '', error: 'boom' } : { ok: true, text: '', error: null };
    };
    const summary = await runLoopCascade({
      loopId: 'loop1',
      nodes: [loopNode({ rounds: 3 }), promptNode('p1', 'b')],
      connections: [edge('loop1', 'p1')],
      caller,
      handlers: silentHandlers,
    });
    expect(round).toBe(1);
    expect(summary).toMatchObject({ completedRounds: 0, failed: true });
  });

  it('cooperative stop halts before the next round', async () => {
    let stop = false;
    const caller = async (): Promise<RunnerResult> => {
      stop = true; // request stop during round 1
      return { ok: true, text: '', error: null };
    };
    const summary = await runLoopCascade({
      loopId: 'loop1',
      nodes: [loopNode({ rounds: 5 }), promptNode('p1', 'b')],
      connections: [edge('loop1', 'p1')],
      caller,
      handlers: silentHandlers,
      shouldStop: () => stop,
    });
    expect(summary).toMatchObject({ completedRounds: 1, stopped: true });
  });

  it('reports each completed round via onRoundComplete', async () => {
    const rounds: number[] = [];
    await runLoopCascade({
      loopId: 'loop1',
      nodes: [loopNode({ rounds: 2, round_start: 5 }), promptNode('p1', 'b')],
      connections: [edge('loop1', 'p1')],
      caller: okCaller,
      handlers: silentHandlers,
      onRoundComplete: (index) => rounds.push(index),
    });
    expect(rounds).toEqual([5, 6]);
  });
});

describe('runLoopCascade — parallel', () => {
  it('caps in-flight rounds at the parallel limit', async () => {
    let active = 0;
    let peak = 0;
    const caller = async (): Promise<RunnerResult> => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 10));
      active -= 1;
      return { ok: true, text: '', error: null };
    };
    const summary = await runLoopCascade({
      loopId: 'loop1',
      nodes: [loopNode({ rounds: 6, mode: 'parallel' }), promptNode('p1', 'b')],
      connections: [edge('loop1', 'p1')],
      caller,
      handlers: silentHandlers,
      parallelLimit: 2,
    });
    expect(peak).toBeLessThanOrEqual(2);
    expect(summary.completedRounds).toBe(6);
  });

  it('aggregates prompt status: one running transition, one final success', async () => {
    const transitions: string[] = [];
    await runLoopCascade({
      loopId: 'loop1',
      nodes: [loopNode({ rounds: 3, mode: 'parallel' }), promptNode('p1', 'b')],
      connections: [edge('loop1', 'p1')],
      caller: okCaller,
      handlers: { onStatusChange: (_id, status) => transitions.push(status) },
      parallelLimit: 3,
    });
    expect(transitions).toEqual(['queued', 'running', 'succeeded']);
  });

  it('restores idle (not succeeded) when stopped before any round completed', async () => {
    const transitions: string[] = [];
    await runLoopCascade({
      loopId: 'loop1',
      nodes: [loopNode({ rounds: 4, mode: 'parallel' }), promptNode('p1', 'b')],
      connections: [edge('loop1', 'p1')],
      caller: okCaller,
      handlers: { onStatusChange: (_id, status) => transitions.push(status) },
      parallelLimit: 2,
      shouldStop: () => true, // stop requested before the pool pulls anything
    });
    expect(transitions).toEqual(['queued', 'running', 'idle']);
  });

  it('marks the prompt failed when any round fails', async () => {
    let n = 0;
    const caller = async (): Promise<RunnerResult> => {
      n += 1;
      return n === 2 ? { ok: false, text: '', error: 'round2 boom' } : { ok: true, text: '', error: null };
    };
    const finals: Array<[string, unknown]> = [];
    const summary = await runLoopCascade({
      loopId: 'loop1',
      nodes: [loopNode({ rounds: 3, mode: 'parallel' }), promptNode('p1', 'b')],
      connections: [edge('loop1', 'p1')],
      caller,
      handlers: {
        onStatusChange: (_id, status, fields) => {
          if (status === 'failed' || status === 'succeeded') finals.push([status, fields.run_error]);
        },
      },
      parallelLimit: 1,
    });
    expect(summary.failed).toBe(true);
    expect(finals).toEqual([['failed', 'round2 boom']]);
  });
});
