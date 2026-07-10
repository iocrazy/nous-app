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

/** React Flow round-trip leftovers that must not travel in a file —
 *  keep in lockstep with the store's RF_INTERNAL_KEYS persist filter. */
const VIEW_ONLY_KEYS = [
  'selected',
  'dragging',
  'measured',
  'width',
  'height',
  'positionAbsolute',
  'className',
] as const;

/** Hard cap on imported nodes — a workflow template is a subgraph, not a
 *  database dump; an unbounded file can freeze the tab and 413 the save. */
export const MAX_WORKFLOW_NODES = 500;

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

export interface ParseWorkflowOptions {
  /** Node `type` whitelist (registry keys). Unknown types are rejected —
   *  they would render nothing or crash the node registry. */
  allowedTypes?: Set<string>;
  /** Reject payloads exported from a different canvas kind. Checked BEFORE
   *  node types so the user sees the real reason, not an "unknown type". */
  expectedKind?: CanvasKind | null;
}

/** URL fields may end up in <img src> / <video src> on every collaborator's
 *  screen once the import persists — allow only http(s) and app-relative. */
function assertSafeUrl(value: unknown, field: string): void {
  if (value == null) return;
  if (typeof value !== 'string') {
    throw new Error(`Workflow file has a malformed node (${field} must be a url string)`);
  }
  const ok = /^https?:\/\//i.test(value) || value.startsWith('/') || value.startsWith('blob:');
  if (!ok) {
    throw new Error(`Workflow file has an unsafe url in ${field}`);
  }
}

function assertOptionalString(value: unknown, field: string): void {
  if (value != null && typeof value !== 'string') {
    throw new Error(`Workflow file has a malformed node (${field} must be a string)`);
  }
}

/** Field-level checks for the data shapes the smart renderers consume.
 *  Wrong TYPES are what crash node views (an object as a React child, a
 *  fake-array images without .map) — and a poisoned node persists and
 *  spreads to every collaborator via realtime, so reject the whole file. */
function assertNodeData(record: Record<string, unknown>): void {
  if (record.data == null) return;
  const data = record.data;
  if (typeof data !== 'object' || Array.isArray(data)) {
    throw new Error('Workflow file has a malformed node (data must be an object)');
  }
  const d = data as Record<string, unknown>;
  assertOptionalString(d.preview_text, 'preview_text');
  assertOptionalString(d.body, 'body');
  assertOptionalString(d.title, 'title');
  assertOptionalString(d.notes, 'notes');
  assertSafeUrl(d.preview_url, 'preview_url');
  if (d.images != null) {
    if (!Array.isArray(d.images)) {
      throw new Error('Workflow file has a malformed node (images must be an array)');
    }
    for (const item of d.images) {
      const img = item as Record<string, unknown> | null;
      if (!img || typeof img !== 'object') {
        throw new Error('Workflow file has a malformed node (images entries must be objects)');
      }
      assertSafeUrl(img.url, 'images[].url');
    }
  }
}

/** Parse + validate a workflow file. Throws user-readable errors — the
 *  file is external input, trust nothing about its shape. */
export function parseWorkflow(
  json: string,
  options: ParseWorkflowOptions = {},
): WorkflowPayload {
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
  const kind = (obj.kind ?? null) as CanvasKind | null;
  if (options.expectedKind && kind && kind !== options.expectedKind) {
    throw new Error(
      `This workflow was exported from a ${kind} canvas and cannot be imported here`,
    );
  }
  if (!Array.isArray(obj.nodes) || obj.nodes.length === 0) {
    throw new Error('Workflow file contains no nodes');
  }
  if (obj.nodes.length > MAX_WORKFLOW_NODES) {
    throw new Error(
      `Workflow file has too many nodes (${obj.nodes.length} — the limit is ${MAX_WORKFLOW_NODES})`,
    );
  }
  const seenIds = new Set<string>();
  for (const node of obj.nodes) {
    const record = node as Record<string, unknown> | null;
    if (!record || typeof record !== 'object' || typeof record.id !== 'string') {
      throw new Error('Workflow file has a malformed node (missing id)');
    }
    if (seenIds.has(record.id)) {
      throw new Error(`Workflow file has duplicate node ids (${record.id})`);
    }
    seenIds.add(record.id);
    const pos = record.position as { x?: unknown; y?: unknown } | undefined;
    if (
      !pos ||
      typeof pos.x !== 'number' ||
      typeof pos.y !== 'number' ||
      !Number.isFinite(pos.x) ||
      !Number.isFinite(pos.y)
    ) {
      throw new Error('Workflow file has a malformed node (missing or non-finite position)');
    }
    if (
      options.allowedTypes &&
      (typeof record.type !== 'string' || !options.allowedTypes.has(record.type))
    ) {
      throw new Error(
        `Workflow file has an unknown node type (${String(record.type)})`,
      );
    }
    assertNodeData(record);
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
    kind,
    nodes: obj.nodes as CanvasNode[],
    connections: connections as CanvasConnection[],
  };
}

/** Suggested download filename, e.g. workflow-3nodes-2026-07-10.json */
export function workflowFilename(nodeCount: number, now: Date = new Date()): string {
  const date = now.toISOString().slice(0, 10);
  return `workflow-${nodeCount}nodes-${date}.json`;
}
