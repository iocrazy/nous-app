import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { LoopMode, LoopNodeData } from '../types';
import { LOOP_MODE_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useNodeDataPatch } from './useNodeDataPatch';

const MODE_OPTIONS: ReadonlyArray<{ value: LoopMode; label: string }> = [
  { value: 'serial', label: 'Serial' },
  { value: 'parallel', label: 'Parallel' },
  { value: 'batch', label: 'Batch' },
];

export function LoopNodeView({ id, data, selected }: NodeProps) {
  const { mode, label } = data as unknown as LoopNodeData;
  const patch = useNodeDataPatch(id);
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
        <select
          className="nodrag rounded border border-slate-200 bg-transparent px-1 py-0 text-[10px] uppercase tracking-wider text-slate-500 outline-none focus:ring-1 focus:ring-indigo-300 dark:border-slate-700 dark:text-slate-400"
          value={mode}
          onChange={(e) => patch({ mode: e.target.value as LoopMode })}
          aria-label="Loop mode"
        >
          {MODE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
      <div className="p-3">
        <input
          className="nodrag w-full bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:ring-1 focus:ring-indigo-300 dark:text-slate-200"
          placeholder="Loop label (optional)"
          value={label}
          onChange={(e) => patch({ label: e.target.value })}
          aria-label="Loop label"
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
