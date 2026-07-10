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
