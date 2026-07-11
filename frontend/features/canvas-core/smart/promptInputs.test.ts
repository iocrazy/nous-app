// features/canvas-core/smart/promptInputs.test.ts
// Upstream image input resolution (G4-F3 — Infinite's cascade refs): a
// prompt fed by an output node that holds a durable generated image uses
// it as the generation source (i2i / i2v).

import { describe, expect, it } from 'vitest';

import { resolveSourceUrl } from './promptInputs';
import type { CanvasConnection, CanvasNode } from '../types';

const NODES: CanvasNode[] = [
  {
    id: 'out1',
    type: 'output',
    position: { x: 0, y: 0 },
    data: { kind: 'image', images: [{ url: '/api/v1/generated-media/11/cover', kind: 'image' }] },
  },
  {
    id: 'out2',
    type: 'output',
    position: { x: 0, y: 200 },
    data: { kind: 'image', preview_url: 'https://external.example.com/x.png' },
  },
  { id: 'p1', type: 'prompt', position: { x: 320, y: 0 }, data: { body: 'x' } },
  { id: 'shot1', type: 'shot', position: { x: 0, y: 400 }, data: { title: 's' } },
];

const conn = (source: string, target: string): CanvasConnection => ({
  id: `${source}-${target}`,
  source,
  target,
  sourceHandle: null,
  targetHandle: null,
});

describe('resolveSourceUrl', () => {
  it('picks the upstream output node durable image', () => {
    expect(resolveSourceUrl('p1', NODES, [conn('out1', 'p1')])).toBe(
      '/api/v1/generated-media/11/cover',
    );
  });

  it('ignores non-durable urls (backend can only bridge generated-media)', () => {
    expect(resolveSourceUrl('p1', NODES, [conn('out2', 'p1')])).toBeNull();
  });

  it('ignores non-output upstreams and returns null with no inputs', () => {
    expect(resolveSourceUrl('p1', NODES, [conn('shot1', 'p1')])).toBeNull();
    expect(resolveSourceUrl('p1', NODES, [])).toBeNull();
  });

  it('first durable upstream wins when there are several', () => {
    expect(
      resolveSourceUrl('p1', NODES, [conn('out2', 'p1'), conn('out1', 'p1')]),
    ).toBe('/api/v1/generated-media/11/cover');
  });
});
