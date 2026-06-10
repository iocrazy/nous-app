import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { PromptNodeData } from '../types';
import { RUN_STATUS_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';

export function PromptNodeView({ data, selected }: NodeProps) {
  const { body, provider_slug, run_status, run_error } =
    data as unknown as PromptNodeData;

  const haloTone = selected
    ? 'border-indigo-500'
    : RUN_STATUS_TONE[run_status];

  return (
    <div
      data-testid="smart-prompt-node"
      className={`rounded-md border-2 bg-white shadow dark:bg-slate-900 ${haloTone}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.prompt }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !bg-slate-400"
      />
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1.5 dark:border-slate-700">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Prompt
        </div>
        <div className="text-[10px] uppercase tracking-wider text-slate-400">
          {run_status}
        </div>
      </div>
      <div className="p-3">
        <div className="line-clamp-4 whitespace-pre-wrap text-sm text-slate-800 dark:text-slate-200">
          {body || (
            <span className="italic text-slate-400">Empty prompt</span>
          )}
        </div>
        <div className="mt-2 flex items-center justify-between text-xs">
          <span className="text-slate-500 dark:text-slate-400">
            {provider_slug || 'no provider'}
          </span>
          {run_error && (
            <span
              className="ml-2 truncate text-rose-600 dark:text-rose-400"
              title={run_error}
            >
              {run_error}
            </span>
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
