import { describe, expect, it } from 'vitest';

import type { CanvasConnection, CanvasNode } from '../types';
import { downstreamPrompts, topoSortPrompts } from './topology';

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
