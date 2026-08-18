// features/canvas-core/smart/nodes/CreateFromNodeBar.tsx
// IC-parity ⑤: the "attached composer" affordance under a selected
// media/group/output card. One key — it spawns a wired image-gen prompt
// right below (the prompt node IS the composer) and selects it.

import { Sparkles } from 'lucide-react';

import { createPromptFromNode } from '../recreate';

export function CreateFromNodeBar({
  nodeId,
  pinned,
  readOnly,
}: {
  nodeId: string;
  /** Pin visible (the node is selected); otherwise hover reveals. */
  pinned?: boolean;
  readOnly?: boolean;
}) {
  if (readOnly) return null;
  return (
    <div
      data-testid="create-from-node-bar"
      className={`canvas-island absolute -bottom-10 left-1/2 z-10 flex -translate-x-1/2 items-center rounded-xl px-1.5 py-1 transition-opacity duration-150 ${
        pinned ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
      }`}
    >
      <button
        type="button"
        aria-label="Create from this"
        title="Create from this"
        onClick={() => createPromptFromNode(nodeId)}
        className="nodrag flex h-6 items-center gap-1 rounded-lg px-1.5 text-[10px] font-semibold text-canvas-text transition-transform hover:-translate-y-px"
      >
        <Sparkles size={13} />
        <span>Create</span>
      </button>
    </div>
  );
}
