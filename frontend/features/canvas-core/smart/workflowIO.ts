// features/canvas-core/smart/workflowIO.ts
//
// Workflow export/import (Infinite-Canvas parity G5, exportSelectedWorkflow /
// importWorkflowFile): a selected subgraph serializes to a self-describing
// JSON payload — internal edges only, view-only fields stripped — and import
// validates hard at the boundary before the caller re-ids it through
// cloneSubgraph. Pure module: no store, no DOM.

import type { CanvasConnection, CanvasKind, CanvasNode } from '../types';

export const WORKFLOW_FORMAT = 'mediahub-canvas-workflow';
export const WORKFLOW_VERSION = 1;

export interface WorkflowPayload {
  format: typeof WORKFLOW_FORMAT;
  version: number;
  kind: CanvasKind | null;
  nodes: CanvasNode[];
  connections: CanvasConnection[];
}

/** React Flow round-trip leftovers that must not travel in a file. */
const VIEW_ONLY_KEYS = ['measured', 'selected', 'dragging', 'className'] as const;

function idOf(node: CanvasNode): string | null {
  const id = (node as { id?: unknown }).id;
  return typeof id === 'string' ? id : null;
}

/** Serialize a selection: deep-cloned nodes minus view fields, plus the
 *  edges internal to the selection (both endpoints included). */
export function serializeWorkflow(
  kind: CanvasKind | null,
  selectedNodes: CanvasNode[],
  allConnections: CanvasConnection[],
): WorkflowPayload {
  const ids = new Set(
    selectedNodes.map(idOf).filter((v): v is string => v !== null),
  );
  const nodes = selectedNodes.map((node) => {
    const clone = JSON.parse(JSON.stringify(node)) as Record<string, unknown>;
    for (const key of VIEW_ONLY_KEYS) delete clone[key];
    return clone as CanvasNode;
  });
  const connections = allConnections
    .filter((c) => ids.has(String(c.source)) && ids.has(String(c.target)))
    .map((c) => JSON.parse(JSON.stringify(c)) as CanvasConnection);
  return {
    format: WORKFLOW_FORMAT,
    version: WORKFLOW_VERSION,
    kind,
    nodes,
    connections,
  };
}

/** Parse + validate a workflow file. Throws user-readable errors — the
 *  file is external input, trust nothing about its shape. */
export function parseWorkflow(json: string): WorkflowPayload {
  let raw: unknown;
  try {
    raw = JSON.parse(json);
  } catch {
    throw new Error('Not a workflow file: invalid JSON');
  }
  if (!raw || typeof raw !== 'object') {
    throw new Error('Not a workflow file: unexpected content');
  }
  const obj = raw as Record<string, unknown>;
  if (obj.format !== WORKFLOW_FORMAT) {
    throw new Error('Not a MediaHub canvas workflow file');
  }
  if (obj.version !== WORKFLOW_VERSION) {
    throw new Error(
      `Unsupported workflow version ${String(obj.version)} — expected ${WORKFLOW_VERSION}`,
    );
  }
  if (!Array.isArray(obj.nodes) || obj.nodes.length === 0) {
    throw new Error('Workflow file contains no nodes');
  }
  for (const node of obj.nodes) {
    const record = node as Record<string, unknown> | null;
    if (!record || typeof record !== 'object' || typeof record.id !== 'string') {
      throw new Error('Workflow file has a malformed node (missing id)');
    }
    const pos = record.position as { x?: unknown; y?: unknown } | undefined;
    if (!pos || typeof pos.x !== 'number' || typeof pos.y !== 'number') {
      throw new Error('Workflow file has a malformed node (missing position)');
    }
  }
  const connections = Array.isArray(obj.connections) ? obj.connections : [];
  for (const conn of connections) {
    const record = conn as Record<string, unknown> | null;
    if (!record || typeof record !== 'object' || record.source == null || record.target == null) {
      throw new Error('Workflow file has a malformed connection');
    }
  }
  return {
    format: WORKFLOW_FORMAT,
    version: WORKFLOW_VERSION,
    kind: (obj.kind ?? null) as CanvasKind | null,
    nodes: obj.nodes as CanvasNode[],
    connections: connections as CanvasConnection[],
  };
}

/** Suggested download filename, e.g. workflow-3nodes-2026-07-10.json */
export function workflowFilename(nodeCount: number, now: Date = new Date()): string {
  const date = now.toISOString().slice(0, 10);
  return `workflow-${nodeCount}nodes-${date}.json`;
}
