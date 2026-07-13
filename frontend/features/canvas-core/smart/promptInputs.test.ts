// features/canvas-core/smart/promptInputs.test.ts
// Upstream image input resolution (G4-F3 — Infinite's cascade refs): a
// prompt fed by an output node that holds a durable generated image uses
// it as the generation source (i2i / i2v).

import { describe, expect, it } from 'vitest';

import { resolveSourceUrl, resolveSourceUrls } from './promptInputs';
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
  {
    id: 'media1',
    type: 'media',
    position: { x: 0, y: 600 },
    data: {
      title: 'Media',
      items: [
        { url: '/api/v1/generated-media/22/cover', kind: 'image' },
        { url: '/api/v1/generated-media/23/stream', kind: 'video' },
        { url: 'https://external.example.com/nope.png', kind: 'image' },
      ],
    },
  },
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

describe('resolveSourceUrls (P1-4 multi-source compare)', () => {
  const MULTI: CanvasNode[] = [
    ...NODES,
    {
      id: 'out3',
      type: 'output',
      position: { x: 0, y: 600 },
      data: {
        kind: 'image',
        images: [
          { url: '/api/v1/generated-media/31/cover', kind: 'image' },
          { url: '/api/v1/generated-media/32/cover', kind: 'image' },
          { url: 'https://external.example.com/skip.png', kind: 'image' },
          { url: '/api/v1/generated-media/40/stream', kind: 'video' },
        ],
      },
    },
    {
      id: 'out4',
      type: 'output',
      position: { x: 0, y: 800 },
      data: {
        kind: 'image',
        // Duplicate of out3's first image — must dedup across nodes.
        images: [{ url: '/api/v1/generated-media/31/cover', kind: 'image' }],
      },
    },
  ];

  it('collects every durable image across all upstream outputs, deduped', () => {
    expect(
      resolveSourceUrls('p1', MULTI, [
        conn('out3', 'p1'),
        conn('out4', 'p1'),
        conn('out1', 'p1'),
      ]),
    ).toEqual([
      '/api/v1/generated-media/31/cover',
      '/api/v1/generated-media/32/cover',
      '/api/v1/generated-media/11/cover',
    ]);
  });

  it('skips non-durable urls and video refs (not comparable as images)', () => {
    const urls = resolveSourceUrls('p1', MULTI, [conn('out3', 'p1')]);
    expect(urls).not.toContain('https://external.example.com/skip.png');
    expect(urls).not.toContain('/api/v1/generated-media/40/stream');
  });

  it('returns [] with no upstream inputs; resolveSourceUrl stays its head', () => {
    expect(resolveSourceUrls('p1', MULTI, [])).toEqual([]);
    expect(resolveSourceUrl('p1', MULTI, [conn('out3', 'p1')])).toBe(
      '/api/v1/generated-media/31/cover',
    );
  });
});

describe('media upload nodes as sources', () => {
  it('uses media-node durable image items, skipping videos and external URLs', () => {
    expect(resolveSourceUrls('p1', NODES, [conn('media1', 'p1')])).toEqual([
      '/api/v1/generated-media/22/cover',
    ]);
  });
});
