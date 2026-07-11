import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { ShotNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useNodeDataPatch } from './useNodeDataPatch';

export function ShotNodeView({ id, data, selected }: NodeProps) {
  const { title, reference_resource_ids, notes } =
    data as unknown as ShotNodeData;
  const patch = useNodeDataPatch(id);

  return (
    <div
      data-testid="smart-shot-node"
      className={`mh-node border-canvas-line ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.shot }}
    >
      <div className="mh-node-head">
        <div className="mh-node-title">Shot</div>
      </div>
      <div className="p-3">
        <input
          // nodrag = React Flow does not start a node drag from this input
          className="nodrag w-full bg-transparent text-sm font-medium text-canvas-text outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40"
          placeholder="Shot title"
          value={title}
          onChange={(e) => patch({ title: e.target.value })}
          aria-label="Shot title"
        />
        {reference_resource_ids.length > 0 && (
          <div className="mt-1 text-xs text-canvas-muted">
            {reference_resource_ids.length} reference
            {reference_resource_ids.length === 1 ? '' : 's'}
          </div>
        )}
        <textarea
          // nowheel = wheel events don't pan the canvas while scrolling the textarea
          className="nodrag nowheel mt-2 w-full resize-none bg-transparent text-xs text-canvas-muted outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40"
          placeholder="Notes (optional)"
          rows={3}
          value={notes}
          onChange={(e) => patch({ notes: e.target.value })}
          aria-label="Shot notes"
        />
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-2 !w-2 !bg-slate-400"
      />
    </div>
  );
}
