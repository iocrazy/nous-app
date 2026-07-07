/**
 * useGroupDragPersist — debounced, lane-split position persistence for a
 * React Flow canvas (extracted from NodesView, Phase B canvas-kit).
 *
 * A node id is routed to a write "lane" by its id prefix (e.g. `sc-`, `ch-`).
 * Each lane owns one async writer; the hook debounces per node id so a flurry
 * of small moves — or a group drag that emits many nodes at once — collapses
 * into one write per node. The timer map is keyed by the full node id, so lanes
 * never collide even when two lanes' entities share a numeric id.
 *
 * The writer receives the id with its lane prefix stripped, plus the rounded
 * position. Nodes whose id matches no lane prefix are ignored. Pending timers
 * are cancelled on unmount (a navigation away drops the last un-elapsed write,
 * matching the original NodesView behavior — the store is re-hydrated on next
 * load, so a dropped debounce is self-healing, not corrupting).
 */
import { useCallback, useEffect, useRef } from 'react';
import type { Node } from '@xyflow/react';

/** Writer for one lane: receives the prefix-stripped id and rounded position. */
export type LaneWriter = (id: string, pos: { x: number; y: number }) => Promise<void>;

export interface GroupDragPersistOptions {
  /** Map of node-id prefix → writer for that lane (e.g. `{ 'sc-': …, 'ch-': … }`). */
  lanes: Record<string, LaneWriter>;
  /** Debounce window per node id, ms. Defaults to 500. */
  debounceMs?: number;
}

/** Default debounce window for persisting a node's dragged coordinates. */
const DEFAULT_DEBOUNCE_MS = 500;

export function useGroupDragPersist({
  lanes,
  debounceMs = DEFAULT_DEBOUNCE_MS,
}: GroupDragPersistOptions): (changed: Node[]) => void {
  // Keep the latest lanes without re-binding persistPositions each render, so
  // callers can pass an inline lanes object without churning the callback.
  const lanesRef = useRef(lanes);
  lanesRef.current = lanes;

  // One pending coordinate write per node id, flushed after the debounce window.
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
    };
  }, []);

  return useCallback(
    (changed: Node[]) => {
      const timers = timersRef.current;
      for (const node of changed) {
        const key = String(node.id);
        const prefix = Object.keys(lanesRef.current).find((p) => key.startsWith(p));
        if (!prefix) continue;
        const write = lanesRef.current[prefix];
        const entityId = key.slice(prefix.length);
        const pos = {
          x: Math.round(node.position.x),
          y: Math.round(node.position.y),
        };
        const existing = timers.get(key);
        if (existing) clearTimeout(existing);
        timers.set(
          key,
          setTimeout(() => {
            timers.delete(key);
            write(entityId, pos).catch((err) =>
              console.error('[useGroupDragPersist] failed to persist node position', err),
            );
          }, debounceMs),
        );
      }
    },
    [debounceMs],
  );
}
