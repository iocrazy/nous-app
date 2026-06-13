import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { PromptNodeData } from '../types';
import { RUN_STATUS_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useNodeDataPatch } from './useNodeDataPatch';

/**
 * Provider options for the dropdown. Bare model IDs use the existing
 * `get_adapter()` prefix dispatch; `nous/<workflow>` triggers the
 * nous-center routing in the canvas-run service (#610).
 *
 * Kept small for now — the AI Library settings page will own the
 * full per-team provider catalogue in a future slice.
 */
const PROVIDER_OPTIONS: ReadonlyArray<{ slug: string; label: string }> = [
  { slug: '', label: 'Default (qwen-plus)' },
  { slug: 'qwen/qwen-plus', label: 'Qwen Plus' },
  { slug: 'qwen/qwen-turbo', label: 'Qwen Turbo' },
  { slug: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6' },
  { slug: 'nous/storyboard', label: 'nous-center · storyboard' },
];

export function PromptNodeView({ id, data, selected }: NodeProps) {
  const { body, provider_slug, run_status, run_error } =
    data as unknown as PromptNodeData;
  const patch = useNodeDataPatch(id);

  const haloTone = selected ? 'border-indigo-500' : RUN_STATUS_TONE[run_status];

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
        <textarea
          // nodrag → React Flow doesn't start a drag from this input
          // nowheel → wheel events scroll the textarea instead of zooming the canvas
          className="nodrag nowheel min-h-[3.5rem] w-full resize-y bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 focus:ring-1 focus:ring-indigo-300 dark:text-slate-200"
          placeholder="What should the model generate?"
          value={body}
          onChange={(e) => patch({ body: e.target.value })}
          aria-label="Prompt body"
          rows={3}
        />
        <div className="mt-2 flex items-center justify-between gap-2 text-xs">
          <select
            className="nodrag flex-1 truncate rounded border border-slate-200 bg-transparent px-1 py-0.5 text-xs text-slate-700 outline-none focus:ring-1 focus:ring-indigo-300 dark:border-slate-700 dark:text-slate-200"
            value={provider_slug}
            onChange={(e) => patch({ provider_slug: e.target.value })}
            aria-label="Prompt provider"
          >
            {PROVIDER_OPTIONS.map((opt) => (
              <option key={opt.slug || '_default'} value={opt.slug}>
                {opt.label}
              </option>
            ))}
          </select>
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
