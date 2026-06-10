/**
 * Tiny helper hook — call `patch({ field: value })` on a smart node
 * without re-deriving its id and the store action every render.
 *
 * Lives inside `smart/nodes/` because each node renderer wires its
 * own inputs through this hook; the store's patchNode action already
 * does the merge-into-data + revision bump + save scheduling.
 */

import { useCallback } from 'react';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';

export function useNodeDataPatch(id: string) {
  const patchNode = useCanvasCoreStore((s) => s.patchNode);
  return useCallback(
    (partialData: Record<string, unknown>) => {
      patchNode(id, { data: partialData });
    },
    [id, patchNode],
  );
}
