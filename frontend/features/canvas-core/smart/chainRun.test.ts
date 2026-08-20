// IC "一键运行" (Run chain): the tail prompt of a cascade gets a second
// button that runs the whole upstream chain in topological order, with a
// cooperative Stop. upstreamChain walks connections backwards through
// intermediate nodes (prompt → output → prompt counts as a chain).
import { beforeEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { isChainTail, startChainRun, upstreamChain, useChainRunStore } from './chainRun';

const P = (id: string, body = 'p') => ({
  id,
  type: 'prompt',
  position: { x: 0, y: 0 },
  data: { body, run_status: 'idle' },
});
const O = (id: string) => ({ id, type: 'output', position: { x: 0, y: 0 }, data: {} });
const E = (id: string, source: string, target: string) => ({ id, source, target });

function seed() {
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    nodes: [P('p1'), O('o1'), P('p2'), O('o2'), P('p3'), P('lone')] as never,
    connections: [
      E('e1', 'p1', 'o1'),
      E('e2', 'o1', 'p2'),
      E('e3', 'p2', 'o2'),
      E('e4', 'o2', 'p3'),
    ] as never,
  });
}

describe('upstreamChain / isChainTail', () => {
  beforeEach(seed);

  it('collects ancestors plus self in topological order', () => {
    const { nodes, connections } = useCanvasCoreStore.getState();
    expect(upstreamChain('p3', nodes, connections)).toEqual(['p1', 'p2', 'p3']);
  });

  it('tail = has upstream prompts and no downstream prompts', () => {
    const { nodes, connections } = useCanvasCoreStore.getState();
    expect(isChainTail('p3', nodes, connections)).toBe(true);
    expect(isChainTail('p2', nodes, connections)).toBe(false);
    expect(isChainTail('lone', nodes, connections)).toBe(false);
  });
});

describe('startChainRun', () => {
  beforeEach(() => {
    seed();
    useChainRunStore.setState({ runningTail: null, stopRequested: false });
  });

  it('runs every prompt in the chain through the caller in order', async () => {
    const ran: string[] = [];
    useChainRunStore.getState().setRunnerOverride(async (ctx) => {
      ran.push(ctx.promptId);
      return { ok: true, text: 'done', error: null };
    });
    await startChainRun('p3');
    expect(ran).toEqual(['p1', 'p2', 'p3']);
    expect(useChainRunStore.getState().runningTail).toBeNull();
  });

  it('stop request halts before the next prompt dispatches', async () => {
    const ran: string[] = [];
    useChainRunStore.getState().setRunnerOverride(async (ctx) => {
      ran.push(ctx.promptId);
      if (ctx.promptId === 'p1') useChainRunStore.getState().requestStop();
      return { ok: true, text: 'done', error: null };
    });
    await startChainRun('p3');
    expect(ran).toEqual(['p1']);
  });
});
