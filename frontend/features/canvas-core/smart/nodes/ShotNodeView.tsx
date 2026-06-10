import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { ShotNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';

export function ShotNodeView({ data, selected }: NodeProps) {
  const { title, reference_resource_ids, notes } = data as unknown as ShotNodeData;
  return (
    <div
      data-testid="smart-shot-node"
      className={`rounded-md border-2 bg-white shadow dark:bg-slate-900 ${
        selected
          ? 'border-indigo-500'
          : 'border-slate-300 dark:border-slate-700'
      }`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.shot }}
    >
      <div className="border-b border-slate-200 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:text-slate-400">
        Shot
      </div>
      <div className="p-3">
        <div className="text-sm font-medium text-slate-900 dark:text-slate-100">
          {title}
        </div>
        {reference_resource_ids.length > 0 && (
          <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {reference_resource_ids.length} reference
            {reference_resource_ids.length === 1 ? '' : 's'}
          </div>
        )}
        {notes && (
          <div className="mt-2 line-clamp-3 text-xs text-slate-600 dark:text-slate-300">
            {notes}
          </div>
        )}
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-2 !w-2 !bg-slate-400"
      />
    </div>
  );
}
