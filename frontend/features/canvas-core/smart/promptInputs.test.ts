// features/canvas-core/smart/promptInputs.test.ts
// Upstream image input resolution (G4-F3 — Infinite's cascade refs): a
// prompt fed by an output node that holds a durable generated image uses
// it as the generation source (i2i / i2v).

import { describe, expect, it } from 'vitest';

import { resolveEffectiveSourceUrl, resolveEffectiveSourceUrls, resolveSourceUrl, resolveSourceUrls, upstreamPromptText } from './promptInputs';
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

describe('groups with absorbed media as sources (group v2)', () => {
  it('uses the group grid durable images, skipping videos/external', () => {
    const nodes: CanvasNode[] = [
      {
        id: 'g1',
        type: 'group',
        position: { x: 0, y: 0 },
        data: {
          label: 'G',
          items: [
            { url: '/api/v1/generated-media/31/cover', kind: 'image' },
            { url: '/api/v1/generated-media/32/stream', kind: 'video' },
            { url: 'https://external.example.com/x.png', kind: 'image' },
          ],
        },
      },
      { id: 'p9', type: 'prompt', position: { x: 320, y: 0 }, data: { body: 'x' } },
    ];
    expect(resolveSourceUrls('p9', nodes, [conn('g1', 'p9')])).toEqual([
      '/api/v1/generated-media/31/cover',
    ]);
  });
});


// ---- resolveEffectiveSourceUrl (IC parity ⑤ — @输入图 selects the i2i src) --

describe('resolveEffectiveSourceUrl', () => {
  const media = {
    id: 'm1',
    type: 'media',
    position: { x: 0, y: 0 },
    data: {
      title: 'Media',
      items: [
        { url: '/api/v1/generated-media/a.png', kind: 'image' },
        { url: '/api/v1/generated-media/b.png', kind: 'image' },
      ],
    },
  } as unknown as CanvasNode;
  const conn = {
    id: 'e1',
    source: 'm1',
    target: 'p1',
    sourceHandle: null,
    targetHandle: null,
  };

  function prompt(source_ref?: string): CanvasNode {
    return {
      id: 'p1',
      type: 'prompt',
      position: { x: 0, y: 0 },
      data: {
        body: 'x',
        provider_slug: '',
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
        ...(source_ref ? { source_ref } : {}),
      },
    } as unknown as CanvasNode;
  }

  it('honors a source_ref that is still among the inputs', () => {
    const p = prompt('/api/v1/generated-media/b.png');
    expect(resolveEffectiveSourceUrl(p, [media, p], [conn])).toBe(
      '/api/v1/generated-media/b.png',
    );
  });

  it('falls back to the first input when source_ref went stale', () => {
    const p = prompt('/api/v1/generated-media/gone.png');
    expect(resolveEffectiveSourceUrl(p, [media, p], [conn])).toBe(
      '/api/v1/generated-media/a.png',
    );
  });

  it('defaults to the first input without a source_ref', () => {
    const p = prompt();
    expect(resolveEffectiveSourceUrl(p, [media, p], [conn])).toBe(
      '/api/v1/generated-media/a.png',
    );
  });

  it('null when the prompt has no image inputs at all', () => {
    const p = prompt('/api/v1/generated-media/a.png');
    expect(resolveEffectiveSourceUrl(p, [p], [])).toBeNull();
  });
});


// ---- manual reference images (IC ⑨C — manualInputRefs) --------------------

describe('manual_refs feed the input chain', () => {
  const conn = {
    id: 'e1',
    source: 'm1',
    target: 'p1',
    sourceHandle: null,
    targetHandle: null,
  };
  const media = {
    id: 'm1',
    type: 'media',
    position: { x: 0, y: 0 },
    data: {
      title: 'Media',
      items: [{ url: '/api/v1/generated-media/a.png', kind: 'image' }],
    },
  } as unknown as CanvasNode;

  it('wired inputs come first, then manual refs, deduped', () => {
    const p = {
      id: 'p1',
      type: 'prompt',
      position: { x: 0, y: 0 },
      data: {
        body: '',
        provider_slug: '',
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
        manual_refs: [
          { url: '/api/v1/generated-media/manual.png', kind: 'image' },
          { url: '/api/v1/generated-media/a.png', kind: 'image' },
        ],
      },
    } as unknown as CanvasNode;
    expect(resolveSourceUrls('p1', [media, p], [conn])).toEqual([
      '/api/v1/generated-media/a.png',
      '/api/v1/generated-media/manual.png',
    ]);
  });

  it('manual refs alone make a prompt an i2i source', () => {
    const p = {
      id: 'p1',
      type: 'prompt',
      position: { x: 0, y: 0 },
      data: {
        body: '',
        provider_slug: '',
        agent_id: null,
        run_status: 'idle',
        resource_refs: [],
        manual_refs: [{ url: '/api/v1/generated-media/solo.png', kind: 'image' }],
      },
    } as unknown as CanvasNode;
    expect(resolveSourceUrls('p1', [p], [])).toEqual([
      '/api/v1/generated-media/solo.png',
    ]);
  });
});


describe('upstreamPromptText', () => {
  it('joins non-empty upstream prompt bodies, ignores non-text cards', () => {
    const up1 = {
      id: 'u1', type: 'prompt', position: { x: 0, y: 0 },
      data: { body: 'a red apple', provider_slug: '', agent_id: null, run_status: 'idle', resource_refs: [] },
    } as unknown as CanvasNode;
    const up2 = {
      id: 'u2', type: 'media', position: { x: 0, y: 0 },
      data: { title: 'Media', items: [] },
    } as unknown as CanvasNode;
    const p = {
      id: 'p1', type: 'prompt', position: { x: 0, y: 0 },
      data: { body: '', provider_slug: '', agent_id: null, run_status: 'idle', resource_refs: [] },
    } as unknown as CanvasNode;
    const conns = [
      { id: 'e1', source: 'u1', target: 'p1', sourceHandle: null, targetHandle: null },
      { id: 'e2', source: 'u2', target: 'p1', sourceHandle: null, targetHandle: null },
    ];
    expect(upstreamPromptText('p1', [up1, up2, p], conns)).toBe('a red apple');
    expect(upstreamPromptText('p1', [p], [])).toBe('');
  });
});


describe('resolveEffectiveSourceUrls (multi-ref, source_ref first)', () => {
  const media = {
    id: 'm1', type: 'media', position: { x: 0, y: 0 },
    data: { title: 'M', items: [
      { url: '/api/v1/generated-media/a.png', kind: 'image' },
      { url: '/api/v1/generated-media/b.png', kind: 'image' },
    ] },
  } as unknown as CanvasNode;
  const conn = { id: 'e', source: 'm1', target: 'p1', sourceHandle: null, targetHandle: null };

  it('source_ref moves to the front, order otherwise preserved', () => {
    const p = {
      id: 'p1', type: 'prompt', position: { x: 0, y: 0 },
      data: { body: '', provider_slug: '', agent_id: null, run_status: 'idle', resource_refs: [], source_ref: '/api/v1/generated-media/b.png' },
    } as unknown as CanvasNode;
    expect(resolveEffectiveSourceUrls(p, [media, p], [conn])).toEqual([
      '/api/v1/generated-media/b.png',
      '/api/v1/generated-media/a.png',
    ]);
  });
});
