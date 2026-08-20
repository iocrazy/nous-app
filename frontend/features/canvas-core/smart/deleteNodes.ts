// features/canvas-core/smart/deleteNodes.ts
//
// Shared node-deletion path (IC parity: the node-delete mini-x and the
// keyboard Delete both run the same routine). Removes the given nodes,
// strips connections that referenced them, and frees children of deleted
// groups back to absolute coords so React Flow doesn't drop them on a
// missing parentId.

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { releaseChildrenOf } from './grouping';

const idOf = (n: unknown): string | null => {
  const v = (n as Record<string, unknown>).id;
  return typeof v === 'string' ? v : null;
};

export function deleteNodesById(ids: string[]): void {
  if (ids.length === 0) return;
  const store = useCanvasCoreStore.getState();
  const doomed = new Set(ids);
  const freed = releaseChildrenOf(store.nodes, doomed);
  const remaining = freed.filter((n) => {
    const id = idOf(n);
    return id !== null && !doomed.has(id);
  });
  const remainingConnections = store.connections.filter((c) => {
    const obj = c as Record<string, unknown>;
    const source = typeof obj.source === 'string' ? obj.source : null;
    const target = typeof obj.target === 'string' ? obj.target : null;
    if (source && doomed.has(source)) return false;
    if (target && doomed.has(target)) return false;
    return true;
  });
  store.setNodes(remaining);
  store.setConnections(remainingConnections);
  const keptSelection = store.selection.filter((s) => !doomed.has(s));
  if (keptSelection.length !== store.selection.length) {
    store.setSelection(keptSelection);
  }
}
