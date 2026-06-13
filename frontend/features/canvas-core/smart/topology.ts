/**
 * Smart-mode topology utilities (Phase 2 Day 4-5).
 *
 * Sorts the prompt nodes of a smart canvas into execution order for
 * Cascade Run. Uses Kahn's algorithm on the prompt-only subgraph so
 * non-prompt nodes (shots / outputs / loops) don't appear in the
 * result but their connections still constrain the ordering.
 */

import type { CanvasConnection, CanvasNode } from '../types';

interface ConnectionEdge {
  source: string;
  target: string;
}

interface TopoOptions {
  /** Limit the topo to this subset of prompt ids — used by "Run from
   *  here" affordances and for excluding already-completed prompts. */
  promptIdAllowlist?: ReadonlySet<string>;
}

export interface TopoResult {
  /** Prompts in dependency-respecting order (sources first). */
  order: string[];
  /** Prompts that ended up in a cycle and couldn't be ordered. */
  cyclic: string[];
}

function asObj(x: unknown): Record<string, unknown> {
  return x as Record<string, unknown>;
}

function nodeId(n: CanvasNode): string | null {
  const id = asObj(n).id;
  return typeof id === 'string' ? id : null;
}

function nodeType(n: CanvasNode): string | null {
  const t = asObj(n).type;
  return typeof t === 'string' ? t : null;
}

function edgeOf(c: CanvasConnection): ConnectionEdge | null {
  const obj = asObj(c);
  if (typeof obj.source !== 'string' || typeof obj.target !== 'string') {
    return null;
  }
  return { source: obj.source, target: obj.target };
}

/**
 * Topo-sort the prompt subgraph using Kahn's algorithm.
 *
 *   1. Walk the directed graph and collapse any path of non-prompt
 *      intermediates (shot → ... → prompt) into a direct prompt-to-
 *      prompt dependency.
 *   2. Run Kahn on the prompt-only graph.
 *
 * The collapse step means a shot that feeds into a prompt does NOT
 * affect ordering (shots are leaves), but a prompt that feeds into a
 * loop that feeds into another prompt DOES — the second prompt comes
 * after the first.
 */
export function topoSortPrompts(
  nodes: CanvasNode[],
  connections: CanvasConnection[],
  options: TopoOptions = {},
): TopoResult {
  const promptIds = new Set<string>();
  const nodeTypeById = new Map<string, string>();
  for (const n of nodes) {
    const id = nodeId(n);
    const type = nodeType(n);
    if (!id || !type) continue;
    nodeTypeById.set(id, type);
    if (type === 'prompt') {
      if (!options.promptIdAllowlist || options.promptIdAllowlist.has(id)) {
        promptIds.add(id);
      }
    }
  }

  // Build the raw forward-adjacency map.
  const forward = new Map<string, Set<string>>();
  for (const c of connections) {
    const e = edgeOf(c);
    if (!e) continue;
    if (!forward.has(e.source)) forward.set(e.source, new Set());
    forward.get(e.source)!.add(e.target);
  }

  // Reachable prompts from each node (collapsing non-prompts).
  const reachablePrompts = new Map<string, Set<string>>();
  function reachable(from: string, seen: Set<string>): Set<string> {
    if (reachablePrompts.has(from)) return reachablePrompts.get(from)!;
    if (seen.has(from)) return new Set(); // cycle break
    seen.add(from);
    const out = new Set<string>();
    const next = forward.get(from);
    if (next) {
      for (const t of next) {
        if (promptIds.has(t)) {
          out.add(t);
        }
        for (const tp of reachable(t, seen)) out.add(tp);
      }
    }
    reachablePrompts.set(from, out);
    return out;
  }
  for (const id of forward.keys()) reachable(id, new Set());

  // Prompt-only adjacency.
  const promptAdj = new Map<string, Set<string>>();
  for (const p of promptIds) promptAdj.set(p, new Set());
  for (const p of promptIds) {
    for (const downstream of reachable(p, new Set())) {
      if (downstream === p) continue;
      promptAdj.get(p)!.add(downstream);
    }
  }

  // Kahn's algorithm on the prompt-only graph.
  const indegree = new Map<string, number>();
  for (const p of promptIds) indegree.set(p, 0);
  for (const [, outs] of promptAdj) {
    for (const t of outs) indegree.set(t, (indegree.get(t) ?? 0) + 1);
  }

  const queue: string[] = [];
  for (const [p, deg] of indegree) {
    if (deg === 0) queue.push(p);
  }
  // Stable ordering for deterministic output across runs.
  queue.sort();

  const order: string[] = [];
  while (queue.length > 0) {
    const next = queue.shift()!;
    order.push(next);
    for (const downstream of promptAdj.get(next) ?? []) {
      const d = (indegree.get(downstream) ?? 0) - 1;
      indegree.set(downstream, d);
      if (d === 0) {
        // Insert in sorted position to keep deterministic order.
        const at = queue.findIndex((q) => q > downstream);
        if (at === -1) queue.push(downstream);
        else queue.splice(at, 0, downstream);
      }
    }
  }

  const cyclic: string[] = [];
  for (const p of promptIds) {
    if (!order.includes(p)) cyclic.push(p);
  }

  return { order, cyclic };
}

/**
 * Walk the graph downstream from `fromId` until each path hits a prompt
 * (or a terminal node), returning those prompt ids. Used by "Run from
 * this node" affordances.
 */
export function downstreamPrompts(
  fromId: string,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): string[] {
  const promptIds = new Set<string>();
  for (const n of nodes) {
    if (nodeType(n) === 'prompt') {
      const id = nodeId(n);
      if (id) promptIds.add(id);
    }
  }
  const forward = new Map<string, string[]>();
  for (const c of connections) {
    const e = edgeOf(c);
    if (!e) continue;
    if (!forward.has(e.source)) forward.set(e.source, []);
    forward.get(e.source)!.push(e.target);
  }
  const seen = new Set<string>();
  const out: string[] = [];
  function walk(id: string) {
    if (seen.has(id)) return;
    seen.add(id);
    for (const t of forward.get(id) ?? []) {
      if (promptIds.has(t)) {
        if (!out.includes(t)) out.push(t);
      }
      walk(t);
    }
  }
  walk(fromId);
  return out;
}
