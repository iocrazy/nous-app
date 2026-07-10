// features/canvas-core/smart/workflowIO.test.ts
// Workflow export/import (Infinite parity G5): a selection serializes to a
// self-describing JSON payload (internal edges only, view fields stripped);
// import validates hard at the boundary and re-ids through cloneSubgraph.

import { describe, expect, it } from 'vitest';

import { parseWorkflow, serializeWorkflow, WORKFLOW_FORMAT } from './workflowIO';
import type { CanvasConnection, CanvasNode } from '../types';

const NODES: CanvasNode[] = [
  {
    id: 'p1',
    type: 'prompt',
    position: { x: 0, y: 0 },
    data: { body: 'a cat' },
    // view-only leftovers that must NOT travel
    measured: { width: 240, height: 120 },
    selected: true,
  } as unknown as CanvasNode,
  { id: 'out1', type: 'output', position: { x: 320, y: 0 }, data: { kind: 'image' } },
];

const CONNECTIONS: CanvasConnection[] = [
  { id: 'e1', source: 'p1', target: 'out1', sourceHandle: null, targetHandle: null },
  { id: 'e2', source: 'p1', target: 'elsewhere', sourceHandle: null, targetHandle: null },
];

describe('serializeWorkflow', () => {
  it('captures the selection with internal edges only and strips view fields', () => {
    const payload = serializeWorkflow('smart', NODES, CONNECTIONS);
    expect(payload.format).toBe(WORKFLOW_FORMAT);
    expect(payload.version).toBe(1);
    expect(payload.kind).toBe('smart');
    expect(payload.nodes).toHaveLength(2);
    expect(payload.connections).toHaveLength(1); // e2's target didn't come along
    const first = payload.nodes[0] as Record<string, unknown>;
    expect(first.measured).toBeUndefined();
    expect(first.selected).toBeUndefined();
  });

  it('deep-clones so later node edits cannot mutate the payload', () => {
    const payload = serializeWorkflow('smart', NODES, CONNECTIONS);
    (NODES[0] as Record<string, unknown>).data = { body: 'MUTATED' };
    expect((payload.nodes[0] as { data: { body: string } }).data.body).toBe('a cat');
  });
});

describe('parseWorkflow', () => {
  it('round-trips a serialized payload', () => {
    const json = JSON.stringify(serializeWorkflow('smart', NODES, CONNECTIONS));
    const parsed = parseWorkflow(json);
    expect(parsed.kind).toBe('smart');
    expect(parsed.nodes).toHaveLength(2);
  });

  it.each([
    ['not json at all', 'not-json{{{'],
    ['wrong format tag', JSON.stringify({ format: 'other', version: 1, kind: 'smart', nodes: [], connections: [] })],
    ['unsupported version', JSON.stringify({ format: WORKFLOW_FORMAT, version: 99, kind: 'smart', nodes: [], connections: [] })],
    ['nodes not an array', JSON.stringify({ format: WORKFLOW_FORMAT, version: 1, kind: 'smart', nodes: 'x', connections: [] })],
    ['node without id', JSON.stringify({ format: WORKFLOW_FORMAT, version: 1, kind: 'smart', nodes: [{ position: { x: 0, y: 0 } }], connections: [] })],
    ['empty nodes', JSON.stringify({ format: WORKFLOW_FORMAT, version: 1, kind: 'smart', nodes: [], connections: [] })],
  ])('rejects %s with a user-readable error', (_label, json) => {
    expect(() => parseWorkflow(json)).toThrowError(/workflow/i);
  });
});

describe('parseWorkflow — hostile input hardening (review HIGH)', () => {
  const wrap = (nodes: unknown[], connections: unknown[] = []) =>
    JSON.stringify({ format: WORKFLOW_FORMAT, version: 1, kind: 'smart', nodes, connections });
  const opts = { allowedTypes: new Set(['shot', 'prompt', 'output', 'loop']) };

  it('rejects unknown node types (registry would render nothing / crash)', () => {
    expect(() =>
      parseWorkflow(wrap([{ id: 'x', type: 'evil', position: { x: 0, y: 0 } }]), opts),
    ).toThrowError(/type/i);
  });

  it('rejects non-object data', () => {
    expect(() =>
      parseWorkflow(wrap([{ id: 'x', type: 'prompt', position: { x: 0, y: 0 }, data: 'str' }]), opts),
    ).toThrowError(/data/i);
  });

  it('rejects a fake-array images object ({length:1} without map → renderer TypeError)', () => {
    expect(() =>
      parseWorkflow(
        wrap([{ id: 'x', type: 'output', position: { x: 0, y: 0 }, data: { kind: 'image', images: { length: 1 } } }]),
        opts,
      ),
    ).toThrowError(/images/i);
  });

  it('rejects non-string preview_text (objects crash React children)', () => {
    expect(() =>
      parseWorkflow(
        wrap([{ id: 'x', type: 'output', position: { x: 0, y: 0 }, data: { preview_text: { a: 1 } } }]),
        opts,
      ),
    ).toThrowError(/preview_text/i);
  });

  it('rejects javascript:/data: URLs in preview_url and images[].url', () => {
    expect(() =>
      parseWorkflow(
        wrap([{ id: 'x', type: 'output', position: { x: 0, y: 0 }, data: { preview_url: 'javascript:alert(1)' } }]),
        opts,
      ),
    ).toThrowError(/url/i);
    expect(() =>
      parseWorkflow(
        wrap([
          { id: 'x', type: 'output', position: { x: 0, y: 0 }, data: { images: [{ url: 'data:text/html,<script>1</script>' }] } },
        ]),
        opts,
      ),
    ).toThrowError(/url/i);
  });

  it('accepts http(s) and app-relative urls', () => {
    const good = wrap([
      { id: 'x', type: 'output', position: { x: 0, y: 0 }, data: { preview_url: '/api/v1/generated-media/1/cover', images: [{ url: 'https://cdn.example.com/a.png' }] } },
    ]);
    expect(() => parseWorkflow(good, opts)).not.toThrow();
  });

  it('rejects duplicate node ids (edge remap would silently mis-wire)', () => {
    expect(() =>
      parseWorkflow(
        wrap([
          { id: 'x', type: 'prompt', position: { x: 0, y: 0 } },
          { id: 'x', type: 'prompt', position: { x: 1, y: 1 } },
        ]),
        opts,
      ),
    ).toThrowError(/duplicate/i);
  });

  it('rejects non-finite positions (Infinity round-trips to null in JSON)', () => {
    expect(() =>
      parseWorkflow(wrap([{ id: 'x', type: 'prompt', position: { x: 1e999, y: 0 } }]), opts),
    ).toThrowError(/position/i);
  });

  it('rejects payloads above the node cap', () => {
    const many = Array.from({ length: 501 }, (_, i) => ({
      id: `n${i}`,
      type: 'prompt',
      position: { x: 0, y: 0 },
    }));
    expect(() => parseWorkflow(wrap(many), opts)).toThrowError(/too many|limit/i);
  });
});

describe('serializeWorkflow — strips the full RF internal set', () => {
  it('drops width/height/positionAbsolute too (store RF_INTERNAL_KEYS parity)', () => {
    const payload = serializeWorkflow(
      'smart',
      [
        {
          id: 'n1',
          type: 'prompt',
          position: { x: 0, y: 0 },
          width: 240,
          height: 120,
          positionAbsolute: { x: 0, y: 0 },
          dragging: true,
        } as unknown as CanvasNode,
      ],
      [],
    );
    const node = payload.nodes[0] as Record<string, unknown>;
    expect(node.width).toBeUndefined();
    expect(node.height).toBeUndefined();
    expect(node.positionAbsolute).toBeUndefined();
    expect(node.dragging).toBeUndefined();
  });
});
