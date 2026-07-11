// features/canvas-core/smart/nodes/GroupNodeView.tsx
//
// Group container (②-3 — Infinite's group node): a translucent, labelled
// region sitting UNDER its members (React Flow renders parents behind
// children). Members ride along on drag via the engine's parentId
// mechanism — this view is pure chrome: dashed hairline, glass tint,
// an editable label chip in the corner.

import type { NodeProps } from '@xyflow/react';

import type { GroupNodeData } from '../types';
import { useNodeDataPatch } from './useNodeDataPatch';

export function GroupNodeView({ id, data, selected }: NodeProps) {
  const { label } = data as unknown as GroupNodeData;
  const patch = useNodeDataPatch(id);

  return (
    <div
      data-testid="smart-group-node"
      className={`h-full w-full rounded-[var(--canvas-r-node)] border border-dashed ${
        selected ? 'mh-node-selected border-canvas-line-strong' : 'border-canvas-line'
      }`}
      style={{ background: 'color-mix(in srgb, var(--canvas-card) 42%, transparent)' }}
    >
      <input
        className="nodrag absolute left-3 top-2 w-32 bg-transparent text-[11px] font-bold uppercase tracking-[0.12em] text-canvas-muted outline-none placeholder:text-canvas-muted/60 focus:text-canvas-text"
        value={label ?? ''}
        placeholder="Group"
        onChange={(e) => patch({ label: e.target.value })}
        aria-label="Group label"
      />
    </div>
  );
}
