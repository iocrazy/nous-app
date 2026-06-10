import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { LoopNodeData } from '../types';
import { LOOP_MODE_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';

const MODE_LABEL: Record<LoopNodeData['mode'], string> = {
  serial: 'Serial',
  parallel: 'Parallel',
  batch: 'Batch',
};

export function LoopNodeView({ data, selected }: NodeProps) {
  const { mode, label } = data as unknown as LoopNodeData;
  const tone = selected ? 'border-indigo-500' : LOOP_MODE_TONE[mode];

  return (
    <div
      data-testid="smart-loop-node"
      className={`rounded-md border-2 bg-white shadow dark:bg-slate-900 ${tone}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.loop }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !bg-slate-400"
      />
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1.5 dark:border-slate-700">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Loop
        </div>
        <div className="text-[10px] uppercase tracking-wider text-slate-400">
          {MODE_LABEL[mode]}
        </div>
      </div>
      <div className="p-3">
        <div className="text-sm text-slate-700 dark:text-slate-200">
          {label || (
            <span className="italic text-slate-400">unnamed loop</span>
          )}
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-2 !w-2 !bg-slate-400"
      />
    </div>
  );
}
