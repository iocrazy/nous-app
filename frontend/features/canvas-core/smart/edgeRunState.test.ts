// features/canvas-core/smart/edgeRunState.test.ts
// Run-state edge colouring (Infinite-Canvas parity Phase 1 G2): edges pick
// up the run status of their adjacent prompt so a cascade visibly flows
// wait → active → done along the wires (Infinite smart-canvas runPath).

import { describe, expect, it } from 'vitest';
import { edgeRunStateClass } from './edgeRunState';

type NodeInfo = { type?: string; runStatus?: string };

function lookup(entries: Record<string, NodeInfo>) {
  const m = new Map(Object.entries(entries));
  return (id: string) => m.get(id);
}

describe('edgeRunStateClass', () => {
  it('maps an edge into a prompt by the prompt run status', () => {
    const nodeInfo = lookup({
      shot1: { type: 'shot' },
      p1: { type: 'prompt', runStatus: 'running' },
    });
    expect(edgeRunStateClass({ source: 'shot1', target: 'p1' }, nodeInfo)).toBe(
      'mh-edge-active',
    );
  });

  it("a finished prompt's edges return to the default stroke (succeeded → no class)", () => {
    const nodeInfo = lookup({
      p1: { type: 'prompt', runStatus: 'succeeded' },
      out1: { type: 'output' },
    });
    expect(edgeRunStateClass({ source: 'p1', target: 'out1' }, nodeInfo)).toBe(null);
  });

  it('prefers the target prompt when both endpoints are prompts', () => {
    const nodeInfo = lookup({
      p1: { type: 'prompt', runStatus: 'succeeded' },
      p2: { type: 'prompt', runStatus: 'queued' },
    });
    expect(edgeRunStateClass({ source: 'p1', target: 'p2' }, nodeInfo)).toBe(
      'mh-edge-wait',
    );
  });

  it('covers the full status map', () => {
    const cases: Array<[string, string | null]> = [
      ['queued', 'mh-edge-wait'],
      ['running', 'mh-edge-active'],
      ['succeeded', null],
      ['failed', 'mh-edge-failed'],
      ['blocked', 'mh-edge-blocked'],
      ['idle', null],
    ];
    for (const [status, expected] of cases) {
      const nodeInfo = lookup({ s: { type: 'shot' }, p: { type: 'prompt', runStatus: status } });
      expect(edgeRunStateClass({ source: 's', target: 'p' }, nodeInfo)).toBe(expected);
    }
  });

  it('returns null when neither endpoint is a prompt', () => {
    const nodeInfo = lookup({
      shot1: { type: 'shot' },
      loop1: { type: 'loop' },
    });
    expect(edgeRunStateClass({ source: 'shot1', target: 'loop1' }, nodeInfo)).toBeNull();
  });

  it('returns null for unknown nodes', () => {
    expect(edgeRunStateClass({ source: 'x', target: 'y' }, lookup({}))).toBeNull();
  });
});
