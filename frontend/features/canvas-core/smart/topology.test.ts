import { describe, expect, it } from 'vitest';

import type { CanvasConnection, CanvasNode } from '../types';
import { downstreamPrompts, topoSort, topoSortPrompts } from './topology';

function n(id: string, type: string): CanvasNode {
  return { id, type } as unknown as CanvasNode;
}
function e(source: string, target: string): CanvasConnection {
  return { id: `${source}->${target}`, source, target } as unknown as CanvasConnection;
}

describe('topoSortPrompts', () => {
  it('empty graph → empty order', () => {
    expect(topoSortPrompts([], [])).toEqual({ order: [], cyclic: [] });
  });

  it('graph with no prompts → empty order', () => {
    const { order, cyclic } = topoSortPrompts(
      [n('s1', 'shot'), n('o1', 'output')],
      [e('s1', 'o1')],
    );
    expect(order).toEqual([]);
    expect(cyclic).toEqual([]);
  });

  it('single chain shot → prompt → output orders just the prompt', () => {
    const { order } = topoSortPrompts(
      [n('s1', 'shot'), n('p1', 'prompt'), n('o1', 'output')],
      [e('s1', 'p1'), e('p1', 'o1')],
    );
    expect(order).toEqual(['p1']);
  });

  it('two parallel prompts come out alphabetically (stable)', () => {
    const { order } = topoSortPrompts(
      [n('s1', 'shot'), n('pB', 'prompt'), n('pA', 'prompt')],
      [e('s1', 'pA'), e('s1', 'pB')],
    );
    expect(order).toEqual(['pA', 'pB']);
  });

  it('prompt → prompt chain respects dependency order', () => {
    const { order } = topoSortPrompts(
      [n('p1', 'prompt'), n('p2', 'prompt'), n('p3', 'prompt')],
      [e('p1', 'p2'), e('p2', 'p3')],
    );
    expect(order).toEqual(['p1', 'p2', 'p3']);
  });

  it('shot does NOT depend on shot order — shots are leaves not roots', () => {
    const { order } = topoSortPrompts(
      [n('sB', 'shot'), n('sA', 'shot'), n('p1', 'prompt')],
      [e('sB', 'p1'), e('sA', 'p1')],
    );
    expect(order).toEqual(['p1']);
  });

  it('loop between prompts is transparent — downstream still orders after upstream', () => {
    // p1 → loop → p2; p1 must come before p2.
    const { order } = topoSortPrompts(
      [n('p1', 'prompt'), n('l1', 'loop'), n('p2', 'prompt')],
      [e('p1', 'l1'), e('l1', 'p2')],
    );
    expect(order).toEqual(['p1', 'p2']);
  });

  it('cyclic prompts surface in cyclic[], not in order', () => {
    const { order, cyclic } = topoSortPrompts(
      [n('p1', 'prompt'), n('p2', 'prompt'), n('p3', 'prompt')],
      [e('p1', 'p2'), e('p2', 'p3'), e('p3', 'p1')],
    );
    expect(order).toEqual([]);
    expect(cyclic.sort()).toEqual(['p1', 'p2', 'p3']);
  });

  it('promptIdAllowlist filters the result + restricts the topo', () => {
    const { order } = topoSortPrompts(
      [n('p1', 'prompt'), n('p2', 'prompt'), n('p3', 'prompt')],
      [e('p1', 'p2'), e('p2', 'p3')],
      { promptIdAllowlist: new Set(['p2', 'p3']) },
    );
    expect(order).toEqual(['p2', 'p3']);
  });

  // CRITICAL regression (outside-voice caveat): byte-identical tie-break on a
  // diamond with ≥3 zero-indegree roots. The shared Kahn core must reproduce
  // the exact alphabetical tie-break — not just the same *set* of nodes.
  it('REGRESSION: ≥3 zero-indegree prompt roots come out in exact alphabetical order', () => {
    // pC, pA, pB all feed pZ (declared out of order on purpose).
    const { order } = topoSortPrompts(
      [n('pC', 'prompt'), n('pA', 'prompt'), n('pB', 'prompt'), n('pZ', 'prompt')],
      [e('pC', 'pZ'), e('pA', 'pZ'), e('pB', 'pZ')],
    );
    expect(order).toEqual(['pA', 'pB', 'pC', 'pZ']);
  });
});

describe('topoSort (generic DAG over all node ids)', () => {
  function edges(...pairs: [string, string][]): { source: string; target: string }[] {
    return pairs.map(([source, target]) => ({ source, target }));
  }

  it('empty graph → empty order', () => {
    expect(topoSort([], [])).toEqual({ order: [], cyclic: [] });
  });

  it('isolated nodes (no edges) come out sorted', () => {
    expect(topoSort(['c', 'a', 'b'], []).order).toEqual(['a', 'b', 'c']);
  });

  it('linear chain a → b → c', () => {
    expect(topoSort(['a', 'b', 'c'], edges(['a', 'b'], ['b', 'c'])).order).toEqual([
      'a',
      'b',
      'c',
    ]);
  });

  it('diamond a → {b,c} → d orders a first, d last, b/c sorted between', () => {
    const { order } = topoSort(
      ['a', 'b', 'c', 'd'],
      edges(['a', 'b'], ['a', 'c'], ['b', 'd'], ['c', 'd']),
    );
    expect(order).toEqual(['a', 'b', 'c', 'd']);
  });

  it('≥3 zero-indegree roots → exact alphabetical tie-break', () => {
    const { order } = topoSort(
      ['r3', 'r1', 'r2', 't'],
      edges(['r3', 't'], ['r1', 't'], ['r2', 't']),
    );
    expect(order).toEqual(['r1', 'r2', 'r3', 't']);
  });

  it('cycle a → b → a surfaces in cyclic[], not order', () => {
    const { order, cyclic } = topoSort(['a', 'b'], edges(['a', 'b'], ['b', 'a']));
    expect(order).toEqual([]);
    expect(cyclic.sort()).toEqual(['a', 'b']);
  });

  it('partial cycle: acyclic prefix orders, cyclic tail surfaces in cyclic[]', () => {
    // root → a → b → a (a,b cyclic); root is orderable.
    const { order, cyclic } = topoSort(
      ['root', 'a', 'b'],
      edges(['root', 'a'], ['a', 'b'], ['b', 'a']),
    );
    expect(order).toEqual(['root']);
    expect(cyclic.sort()).toEqual(['a', 'b']);
  });

  it('edges to/from ids not in the node set are ignored', () => {
    const { order } = topoSort(['a', 'b'], edges(['a', 'b'], ['a', 'ghost'], ['x', 'b']));
    expect(order).toEqual(['a', 'b']);
  });
});

describe('downstreamPrompts', () => {
  it('walks past shots / loops / outputs and collects only prompts', () => {
    const nodes = [
      n('s1', 'shot'),
      n('p1', 'prompt'),
      n('l1', 'loop'),
      n('p2', 'prompt'),
      n('o1', 'output'),
    ];
    const edges = [e('s1', 'p1'), e('p1', 'l1'), e('l1', 'p2'), e('p2', 'o1')];
    expect(downstreamPrompts('s1', nodes, edges)).toEqual(['p1', 'p2']);
  });

  it('returns [] when start node has no downstream', () => {
    expect(
      downstreamPrompts('isolated', [n('isolated', 'shot')], []),
    ).toEqual([]);
  });
});
