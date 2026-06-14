/**
 * ClassicMode cascade orchestrator (Phase 5a B5).
 *
 * Runs the classic node graph in topological order, dispatching each
 * runnable node through the synchronous `ClassicRunner` seam. The
 * ENG-CRITICAL contract: a failure is NEVER silent. When a node fails we
 *   1. mark it `failed` with its inline `run_error` (B4 vocabulary),
 *   2. mark every DOWNSTREAM node `blocked` (skipped because upstream
 *      failed) — computed here, OUTSIDE the run loop, because the flat
 *      SmartMode `runPrompts` is downstream-blind, and
 *   3. surface a top-level toast `Cascade stopped at <label>`.
 *
 * Unlike `runPrompts` (which first-error breaks), this does NOT stop the
 * whole cascade on a failure — it only blocks the failed node's downstream
 * closure and keeps running INDEPENDENT branches (e.g. a sibling in a
 * diamond that is not downstream of the failure still runs).
 *
 * Execution stays SINGLE-SYNCHRONOUS: every node (incl. comfy) runs through
 * the existing synchronous run path. No async/DBOS.
 */

import { topoSort } from '../smart/topology';
import type { CanvasConnection, CanvasNode } from '../types';
import { beginAbortable, clearAbortController } from './abortRegistry';
import { dispatchClassicNode } from './classicDispatch';
import { getClassicNodeDefinition } from './registry';
import type { ClassicRunStatus } from './nodes/ClassicNodeShell';
import type { ClassicRunner } from './classicRunner';

const DEFAULT_NOW = (): string => new Date().toISOString();

export interface CascadeNodeStatusPatch {
  run_status: ClassicRunStatus;
  run_started_at?: string | null;
  run_error?: string | null;
}

export interface CascadeHandlers {
  /** Patch one node's run fields. The surface wires this to the canvas
   *  store's `patchNode(id, { data: patch })`. */
  onNodePatch(nodeId: string, patch: CascadeNodeStatusPatch): void;
  /** Surface a top-level toast. canvas-core has no toast of its own, so
   *  the invoking surface maps this to the app `useToast`. Optional so the
   *  cascade is usable headless. */
  onToast?(message: string): void;
  /** Pinned by tests. Defaults to wall-clock ISO. */
  now?(): string;
}

export interface CascadeReport {
  succeeded: string[];
  failed: string[];
  blocked: string[];
  /** Passive (literal/sink) nodes and isolated-unknown nodes that were not
   *  dispatched. */
  skipped: string[];
  /** Top-level toast messages produced (mirrors onToast calls). */
  toasts: string[];
}

// ---------------------------------------------------------------------------
// Graph helpers
// ---------------------------------------------------------------------------

function readId(node: CanvasNode): string | null {
  const id = (node as Record<string, unknown>).id;
  return typeof id === 'string' ? id : null;
}

function readType(node: CanvasNode): string | undefined {
  const t = (node as Record<string, unknown>).type;
  return typeof t === 'string' ? t : undefined;
}

function readData(node: CanvasNode): Record<string, unknown> {
  const d = (node as Record<string, unknown>).data;
  return d && typeof d === 'object' ? (d as Record<string, unknown>) : {};
}

function readEdge(conn: CanvasConnection): { source: string; target: string } | null {
  const obj = conn as Record<string, unknown>;
  if (typeof obj.source !== 'string' || typeof obj.target !== 'string') return null;
  return { source: obj.source, target: obj.target };
}

/** Build the forward adjacency (source → targets) over node ids. */
function buildForwardAdjacency(
  edges: { source: string; target: string }[],
): Map<string, Set<string>> {
  const forward = new Map<string, Set<string>>();
  for (const { source, target } of edges) {
    if (!forward.has(source)) forward.set(source, new Set());
    forward.get(source)!.add(target);
  }
  return forward;
}

/**
 * Downstream closure of `fromId` over outgoing edges — every node REACHABLE
 * from it, EXCLUDING itself. This is the NEW logic layered outside the flat
 * runner: when a node fails, every node here is `blocked`.
 */
function downstreamClosure(
  fromId: string,
  forward: Map<string, Set<string>>,
): Set<string> {
  const out = new Set<string>();
  const stack = [...(forward.get(fromId) ?? [])];
  while (stack.length > 0) {
    const next = stack.pop()!;
    if (out.has(next)) continue;
    out.add(next);
    for (const t of forward.get(next) ?? []) {
      if (!out.has(t)) stack.push(t);
    }
  }
  out.delete(fromId); // a cycle can route back; never block the source itself
  return out;
}

/** Human label for the toast: explicit data label/title, else the registry
 *  label for the node type, else the raw node id. */
function nodeLabel(node: CanvasNode, nodeType: string | undefined): string {
  const data = readData(node);
  for (const key of ['label', 'title']) {
    const v = data[key];
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  const def = getClassicNodeDefinition(nodeType);
  if (def) return def.label;
  return readId(node) ?? 'node';
}

// ---------------------------------------------------------------------------
// Orchestrator
// ---------------------------------------------------------------------------

/**
 * Run the classic cascade. Resolves with a {@link CascadeReport}; never
 * throws for graph/dispatch/run errors (an unknown node type or a node run
 * failure is CONTAINED, not fatal).
 */
export async function runClassicCascade(
  nodes: CanvasNode[],
  connections: CanvasConnection[],
  runner: ClassicRunner,
  handlers: CascadeHandlers,
): Promise<CascadeReport> {
  const now = handlers.now ?? DEFAULT_NOW;

  const nodeById = new Map<string, CanvasNode>();
  for (const n of nodes) {
    const id = readId(n);
    if (id) nodeById.set(id, n);
  }
  const nodeIds = [...nodeById.keys()];

  const edges = connections
    .map(readEdge)
    .filter((e): e is { source: string; target: string } => e !== null);
  const forward = buildForwardAdjacency(edges);

  const { order } = topoSort(nodeIds, edges);

  const report: CascadeReport = {
    succeeded: [],
    failed: [],
    blocked: [],
    skipped: [],
    toasts: [],
  };
  const blocked = new Set<string>();

  const emitToast = (message: string): void => {
    report.toasts.push(message);
    handlers.onToast?.(message);
  };

  /** Mark the failed node + block its downstream closure. */
  const containFailure = (
    nodeId: string,
    node: CanvasNode,
    nodeType: string | undefined,
    runError: string,
  ): void => {
    report.failed.push(nodeId);
    handlers.onNodePatch(nodeId, { run_status: 'failed', run_error: runError });
    for (const downstreamId of downstreamClosure(nodeId, forward)) {
      if (blocked.has(downstreamId)) continue;
      blocked.add(downstreamId);
      report.blocked.push(downstreamId);
      handlers.onNodePatch(downstreamId, { run_status: 'blocked', run_error: null });
    }
    emitToast(`Cascade stopped at ${nodeLabel(node, nodeType)}`);
  };

  for (const nodeId of order) {
    if (blocked.has(nodeId)) continue; // already blocked upstream → skip

    const node = nodeById.get(nodeId);
    if (!node) continue;
    const nodeType = readType(node);
    const dispatch = dispatchClassicNode(nodeType, readData(node));

    if (dispatch.kind === 'passive') {
      // Literal/sink node — not an execution step. Pass through silently.
      report.skipped.push(nodeId);
      continue;
    }

    if (dispatch.kind === 'unknown') {
      const hasDownstream = (forward.get(nodeId)?.size ?? 0) > 0;
      if (hasDownstream) {
        // Most honest: a contained failure that blocks the dependents it
        // would otherwise feed garbage.
        containFailure(nodeId, node, nodeType, dispatch.reason);
      } else {
        // Isolated unknown — nothing downstream to protect. Skip loudly.
        // eslint-disable-next-line no-console
        console.warn(
          `[classic cascade] skipping unknown node '${nodeId}' (${dispatch.reason})`,
        );
        report.skipped.push(nodeId);
      }
      continue;
    }

    if (dispatch.kind === 'invalid') {
      // Runnable TYPE, unrunnable config (e.g. comfy w/o workflow_slug).
      containFailure(nodeId, node, nodeType, dispatch.reason);
      continue;
    }

    // dispatch.kind === 'run' — dispatch through the synchronous runner.
    handlers.onNodePatch(nodeId, {
      run_status: 'running',
      run_started_at: now(),
      run_error: null,
    });

    const controller = beginAbortable(nodeId);
    let result;
    try {
      result = await runner(
        {
          nodeId,
          nodeType,
          body: readBody(node),
          providerSlug: dispatch.providerSlug,
          agentId: readAgentId(node),
        },
        controller.signal,
      );
    } catch (err) {
      result = {
        ok: false,
        text: '',
        error: err instanceof Error ? err.message : String(err),
      };
    } finally {
      clearAbortController(nodeId);
    }

    if (result.ok) {
      report.succeeded.push(nodeId);
      handlers.onNodePatch(nodeId, { run_status: 'succeeded', run_error: null });
    } else {
      containFailure(nodeId, node, nodeType, result.error ?? 'run failed');
    }
  }

  return report;
}

function readBody(node: CanvasNode): string {
  const data = readData(node);
  for (const key of ['body', 'prompt', 'text']) {
    const v = data[key];
    if (typeof v === 'string') return v;
  }
  return '';
}

function readAgentId(node: CanvasNode): string | null {
  const data = readData(node);
  const v = data.agent_id ?? data.agentId;
  return typeof v === 'string' ? v : null;
}
