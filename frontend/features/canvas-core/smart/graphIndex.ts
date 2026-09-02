// features/canvas-core/smart/graphIndex.ts
//
// Shared per-graph index (Wave 1+2 Task 4 — render cost).
//
// Every prompt card used to derive its own view of the graph with a fresh
// `new Map(nodes)` per node per render. A canvas with P prompts therefore
// paid O(P × N) map construction on every drag frame, and — worse — each
// card subscribed to the whole `s.nodes` array to do it, so a drag tick on
// one node re-rendered all of them.
//
// This module builds the index ONCE per (nodes, connections) pair and
// memoises it on the two array references. Nested WeakMaps: the outer key is
// the nodes array, the inner the connections array, so a store reset or a
// second canvas simply misses and both entries are collectable.

import type { CanvasConnection, CanvasNode } from '../types';

const asObj = (n: unknown) => n as Record<string, unknown>;

export interface GraphIndex {
  readonly nodes: CanvasNode[];
  readonly connections: CanvasConnection[];
  /** node id → node. */
  readonly byId: ReadonlyMap<string, CanvasNode>;
  /** target node id → the connections landing on it, in connection order. */
  readonly incoming: ReadonlyMap<string, CanvasConnection[]>;
  /** Lazily filled by {@link topologyKeyOf} — see there. */
  topologyKey: string | null;
}

const indexCache = new WeakMap<object, WeakMap<object, GraphIndex>>();

/** The index for this exact (nodes, connections) pair, built at most once. */
export function graphIndexFor(
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): GraphIndex {
  let inner = indexCache.get(nodes);
  if (!inner) {
    inner = new WeakMap<object, GraphIndex>();
    indexCache.set(nodes, inner);
  }
  const hit = inner.get(connections);
  if (hit) return hit;

  const byId = new Map<string, CanvasNode>();
  for (const n of nodes) byId.set(String(asObj(n).id), n);

  const incoming = new Map<string, CanvasConnection[]>();
  for (const c of connections) {
    const target = String(asObj(c).target);
    const list = incoming.get(target);
    if (list) list.push(c);
    else incoming.set(target, [c]);
  }

  const index: GraphIndex = {
    nodes,
    connections,
    byId,
    incoming,
    topologyKey: null,
  };
  inner.set(connections, index);
  return index;
}

/**
 * A key over everything the graph's SHAPE depends on: each node's id and
 * type, plus each edge's source and target. Node `data` and `position` are
 * deliberately excluded.
 *
 * That is the exact input surface of `isChainTail` / `topoSortPrompts`
 * (verified: both read only `id` / `type` / `source` / `target`), so a
 * result keyed on it survives the whole drag — during which positions churn
 * every frame but the shape never changes. Computed lazily and cached on the
 * index so the O(N+E) string build happens once per tick, not once per card.
 */
export function topologyKeyOf(index: GraphIndex): string {
  if (index.topologyKey !== null) return index.topologyKey;
  const parts: string[] = [];
  for (const n of index.nodes) {
    const o = asObj(n);
    parts.push(`${String(o.id)}:${String(o.type)}`);
  }
  parts.push('|');
  for (const c of index.connections) {
    const o = asObj(c);
    parts.push(`${String(o.source)}>${String(o.target)}`);
  }
  index.topologyKey = parts.join(',');
  return index.topologyKey;
}
