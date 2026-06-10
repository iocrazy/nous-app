import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { OutputNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';

const KIND_LABEL: Record<OutputNodeData['kind'], string> = {
  text: 'Text',
  image: 'Image',
  video: 'Video',
  audio: 'Audio',
};

export function OutputNodeView({ data, selected }: NodeProps) {
  const { kind, resource_id, preview_text } = data as unknown as OutputNodeData;

  return (
    <div
      data-testid="smart-output-node"
      className={`rounded-md border-2 bg-white shadow dark:bg-slate-900 ${
        selected
          ? 'border-indigo-500'
          : 'border-slate-300 dark:border-slate-700'
      }`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.output }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !bg-slate-400"
      />
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1.5 dark:border-slate-700">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Output · {KIND_LABEL[kind]}
        </div>
        {resource_id && (
          <div className="text-[10px] uppercase tracking-wider text-emerald-600">
            Saved
          </div>
        )}
      </div>
      <div className="p-3">
        {preview_text ? (
          <div className="line-clamp-4 whitespace-pre-wrap text-sm text-slate-800 dark:text-slate-200">
            {preview_text}
          </div>
        ) : (
          <div className="text-xs italic text-slate-400">
            {kind === 'text' ? 'No text yet' : `No ${kind} rendered yet`}
          </div>
        )}
      </div>
    </div>
  );
}
