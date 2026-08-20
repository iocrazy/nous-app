// features/canvas-core/smart/nodes/NodeDeleteButton.tsx
//
// IC parity (smart-canvas.js node-delete mini-x): every creative node
// carries a small trash button at its top-right corner, dimmed until the
// node is hovered or selected. Clicking deletes the node through the same
// shared routine as keyboard Delete (undo via canvas snapshot history).

import { Trash2 } from 'lucide-react';

import { deleteNodesById } from '../deleteNodes';

export function NodeDeleteButton({
  nodeId,
  readOnly,
}: {
  nodeId: string;
  readOnly?: boolean;
}) {
  if (readOnly) return null;
  return (
    <button
      type="button"
      data-testid="node-delete"
      aria-label="Delete node"
      title="Delete node"
      onClick={(e) => {
        e.stopPropagation();
        deleteNodesById([nodeId]);
      }}
      className="nodrag nopan absolute -right-2.5 -top-2.5 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-canvas-line bg-canvas-card text-canvas-muted opacity-0 shadow transition-opacity duration-150 hover:border-rose-400/60 hover:text-rose-500 focus-visible:opacity-100 group-hover:opacity-100"
    >
      <Trash2 size={11} />
    </button>
  );
}
