/**
 * Connection <-> React Flow edge mapping + connection-validation (Phase 5a B1).
 *
 * Split out of `CanvasSurface` so the pure logic is unit-testable and so
 * the handle ids (`sourceHandle` / `targetHandle`) thread cleanly through
 * the validation path.
 *
 * ClassicMode nodes have MULTIPLE typed ports (image / text / prompt), so
 * a wire must remember WHICH handle it connects to. SmartMode has no
 * handle ids; the round-trip and validation are a no-op for it.
 */

import type { Connection, Edge } from '@xyflow/react';

import type { CanvasConnection, CanvasKind } from '../types';
import { canConnectSmart } from '../smart/types';

/**
 * Map stored `CanvasConnection`s into React Flow edges, preserving the
 * typed-port handle ids so they round-trip. Handle fields are only
 * emitted when present — SmartMode connections (no handles) come out
 * byte-identical to before.
 */
export function toReactFlowEdges(connections: CanvasConnection[]): Edge[] {
  return connections.map((conn, idx) => {
    const obj = conn as Record<string, unknown>;
    const id = typeof obj.id === 'string' ? obj.id : `edge-${idx}`;
    const source = typeof obj.source === 'string' ? obj.source : '';
    const target = typeof obj.target === 'string' ? obj.target : '';
    const edge: Edge = { id, source, target };
    if (typeof obj.sourceHandle === 'string') edge.sourceHandle = obj.sourceHandle;
    if (typeof obj.targetHandle === 'string') edge.targetHandle = obj.targetHandle;
    return edge;
  });
}

/**
 * Validate a proposed (or existing) connection. Accepts the full React
 * Flow `Connection` / `Edge` shape so the handle ids are AVAILABLE to the
 * validator — ClassicMode port-typed validation (B2) will read
 * `connection.sourceHandle` / `connection.targetHandle`.
 *
 * For SmartMode the rule is purely node-type based (handles ignored,
 * which is harmless). For every other kind we currently allow any
 * connection.
 */
export function validateCanvasConnection(
  connection: Connection | Edge,
  kind: CanvasKind | null,
  nodeTypeById: (id: string) => string | undefined,
): boolean {
  if (kind !== 'smart') return true;
  return canConnectSmart(
    nodeTypeById(connection.source),
    nodeTypeById(connection.target),
  );
}
