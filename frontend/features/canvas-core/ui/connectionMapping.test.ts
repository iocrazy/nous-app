/**
 * Connection-mapping unit tests (Phase 5a B1).
 *
 * ClassicMode nodes have MULTIPLE typed ports (image / text / prompt),
 * so the React Flow handle ids (`sourceHandle` / `targetHandle`) must
 * survive into the stored `CanvasConnection` AND be reachable by the
 * connection-validation path. SmartMode has no handle ids — passing them
 * through must be a no-op for it.
 */

import { describe, expect, it, vi } from 'vitest';

import type { Connection } from '@xyflow/react';

import type { CanvasConnection } from '../types';
import { toReactFlowEdges, validateCanvasConnection } from './connectionMapping';

function conn(fields: Record<string, unknown>): CanvasConnection {
  return fields as unknown as CanvasConnection;
}

describe('toReactFlowEdges', () => {
  it('preserves sourceHandle/targetHandle so typed ports round-trip', () => {
    const [edge] = toReactFlowEdges([
      conn({
        id: 'e1',
        source: 'a',
        target: 'b',
        sourceHandle: 'image-out',
        targetHandle: 'image-in',
      }),
    ]);
    expect(edge).toMatchObject({
      id: 'e1',
      source: 'a',
      target: 'b',
      sourceHandle: 'image-out',
      targetHandle: 'image-in',
    });
  });

  it('omits handle fields when a connection has none (SmartMode no-op)', () => {
    const [edge] = toReactFlowEdges([conn({ id: 'e1', source: 'a', target: 'b' })]);
    expect(edge.sourceHandle).toBeUndefined();
    expect(edge.targetHandle).toBeUndefined();
    expect(edge).toEqual({ id: 'e1', source: 'a', target: 'b' });
  });

  it('round-trips handles back through the store cast (Edge[] -> CanvasConnection[])', () => {
    const edges = toReactFlowEdges([
      conn({
        id: 'e1',
        source: 'p1',
        target: 'o1',
        sourceHandle: 'h1',
        targetHandle: 'h2',
      }),
    ]);
    // The store's onEdgesChange casts the React Flow edges straight back
    // into CanvasConnection[] — handles must survive that cast.
    const stored = edges as unknown as CanvasConnection[];
    const obj = stored[0] as Record<string, unknown>;
    expect(obj.sourceHandle).toBe('h1');
    expect(obj.targetHandle).toBe('h2');
  });
});

describe('validateCanvasConnection', () => {
  const typeOf =
    (m: Record<string, string>) =>
    (id: string): string | undefined =>
      m[id];

  it('non-smart mode: always valid, and the full handle-bearing connection is available', () => {
    const connection: Connection = {
      source: 'n1',
      target: 'n2',
      sourceHandle: 'image-out',
      targetHandle: 'text-in',
    };
    // The handle ids are reachable on the argument the validator receives
    // — this is what B2 (ClassicMode port validation) will consume.
    expect(connection.sourceHandle).toBe('image-out');
    expect(connection.targetHandle).toBe('text-in');
    expect(validateCanvasConnection(connection, 'classic', typeOf({}))).toBe(true);
  });

  it('smart mode: validates by node type even when handle ids are present', () => {
    const types = typeOf({ s1: 'shot', p1: 'prompt', o1: 'output' });
    expect(
      validateCanvasConnection(
        { source: 's1', target: 'p1', sourceHandle: 'x', targetHandle: 'y' },
        'smart',
        types,
      ),
    ).toBe(true);
    // output -> anything is terminal; handles ignored, node type wins.
    expect(
      validateCanvasConnection(
        { source: 'o1', target: 'p1', sourceHandle: 'x', targetHandle: 'y' },
        'smart',
        types,
      ),
    ).toBe(false);
    // shot -> output must go through a prompt.
    expect(
      validateCanvasConnection(
        { source: 's1', target: 'o1', sourceHandle: null, targetHandle: null },
        'smart',
        types,
      ),
    ).toBe(false);
  });

  it('smart mode: resolves the node type for both endpoints', () => {
    const resolver = vi.fn((id: string) => (id === 's1' ? 'shot' : 'prompt'));
    validateCanvasConnection(
      { source: 's1', target: 'p1', sourceHandle: 'a', targetHandle: 'b' },
      'smart',
      resolver,
    );
    expect(resolver).toHaveBeenCalledWith('s1');
    expect(resolver).toHaveBeenCalledWith('p1');
  });

  it('null kind: treated as non-smart → valid', () => {
    expect(
      validateCanvasConnection(
        { source: 'a', target: 'b', sourceHandle: null, targetHandle: null },
        null,
        typeOf({}),
      ),
    ).toBe(true);
  });
});
