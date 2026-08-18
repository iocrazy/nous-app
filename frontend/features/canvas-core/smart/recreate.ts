// features/canvas-core/smart/recreate.ts
// IC-parity ⑤ (点击节点出现再创作面板): one action spawns the attached
// composer. Infinite renders an inline panel under the selected node; the
// nous prompt node already IS that panel (body / @-mentions / gen settings
// / in-node Run), so re-creation = spawn a wired image-gen prompt right
// below the source and hand it the selection.

import { createPromptNode } from './factories';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasConnection, CanvasNode } from '../types';

const BELOW_GAP = 32;
const FALLBACK_H = 200;

/** Spawn an image-gen prompt below ``nodeId``, wire source → prompt, and
 *  select it. Returns the new prompt id, or null for an unknown node. */
export function createPromptFromNode(nodeId: string): string | null {
  const { nodes, connections, setNodes, setConnections, setSelection } =
    useCanvasCoreStore.getState();
  const source = nodes.find((n) => String((n as { id: unknown }).id) === nodeId);
  if (!source) return null;
  const obj = source as unknown as {
    position: { x: number; y: number };
    parentId?: string;
    measured?: { height?: number };
    style?: { height?: number };
  };
  // A grouped member's position is parent-relative; the spawned prompt is a
  // top-level node, so lift the origin to absolute coords first.
  let originX = obj.position.x;
  let originY = obj.position.y;
  if (obj.parentId) {
    const parent = nodes.find(
      (n) => String((n as { id: unknown }).id) === obj.parentId,
    ) as unknown as { position: { x: number; y: number } } | undefined;
    if (parent) {
      originX += parent.position.x;
      originY += parent.position.y;
    }
  }
  const height = obj.measured?.height ?? obj.style?.height ?? FALLBACK_H;
  const prompt = createPromptNode(
    { gen: { kind: 'image', model: '', ratio: '1:1', count: 1 } },
    { position: { x: originX, y: originY + height + BELOW_GAP } },
  );
  const edge: CanvasConnection = {
    id: `edge-${crypto.randomUUID()}`,
    source: nodeId,
    target: prompt.id,
    sourceHandle: null,
    targetHandle: null,
  };
  setNodes([...nodes, prompt as unknown as CanvasNode]);
  setConnections([...connections, edge]);
  setSelection([prompt.id]);
  return prompt.id;
}
