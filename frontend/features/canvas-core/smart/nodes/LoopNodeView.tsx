import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Plus, X } from 'lucide-react';

import type { LoopMode, LoopNodeData } from '../types';
import { LOOP_MODE_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { clampRoundStart, clampRounds } from '../loopVars';
import { useNodeDataPatch } from './useNodeDataPatch';

const MODE_OPTIONS: ReadonlyArray<{ value: LoopMode; label: string }> = [
  { value: 'serial', label: 'Serial' },
  { value: 'parallel', label: 'Parallel' },
  { value: 'batch', label: 'Batch' },
];

export function LoopNodeView({ id, data, selected }: NodeProps) {
  const { mode, label, rounds, round_start, prompts } = data as unknown as LoopNodeData;
  const patch = useNodeDataPatch(id);
  const tone = selected ? 'border-indigo-500' : LOOP_MODE_TONE[mode];
  // Canvases persisted before G3a lack the batch fields — default in view.
  const safeRounds = clampRounds(rounds ?? 1);
  const safeStart = clampRoundStart(round_start ?? 1);
  const safePrompts = prompts && prompts.length > 0 ? prompts : [''];

  const patchPrompt = (index: number, value: string) => {
    patch({ prompts: safePrompts.map((p, i) => (i === index ? value : p)) });
  };

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
      <div className="space-y-2 p-3">
        <input
          className="nodrag w-full bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:ring-1 focus:ring-indigo-300 dark:text-slate-200"
          placeholder="Loop label (optional)"
          value={label}
          onChange={(e) => patch({ label: e.target.value })}
          aria-label="Loop label"
        />

        <div className="flex items-center gap-2">
          <NumberField
            label="Rounds"
            ariaLabel="Loop rounds"
            value={safeRounds}
            max={100}
            onChange={(v) => patch({ rounds: clampRounds(v) })}
          />
          <NumberField
            label="Start"
            ariaLabel="Start index"
            value={safeStart}
            max={9999}
            onChange={(v) => patch({ round_start: clampRoundStart(v) })}
          />
        </div>

        {/* Rotating prompt list — round N uses entry (N-1) % count. The
            《计数》/《总数》/《进度》 tokens are replaced per round. */}
        <div className="space-y-1">
          {safePrompts.map((prompt, i) => (
            <div key={i} className="flex items-start gap-1">
              <span className="pt-1.5 text-[10px] text-slate-400">{i + 1}</span>
              <textarea
                className="nodrag min-h-[34px] w-full resize-y rounded border border-slate-200 bg-transparent px-1.5 py-1 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:ring-1 focus:ring-indigo-300 dark:border-slate-700 dark:text-slate-200"
                placeholder="Prompt for this round — 《计数》 = round index"
                value={prompt}
                rows={1}
                onChange={(e) => patchPrompt(i, e.target.value)}
                aria-label={`Loop prompt ${i + 1}`}
              />
              <button
                type="button"
                className="nodrag mt-1 rounded p-0.5 text-slate-400 hover:text-rose-500 disabled:cursor-not-allowed disabled:opacity-40"
                onClick={() => patch({ prompts: safePrompts.filter((_p, j) => j !== i) })}
                disabled={safePrompts.length <= 1}
                aria-label={`Remove prompt ${i + 1}`}
              >
                <X size={12} />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="nodrag flex items-center gap-1 rounded px-1 py-0.5 text-[11px] text-slate-500 hover:text-indigo-500 dark:text-slate-400"
            onClick={() => patch({ prompts: [...safePrompts, ''] })}
            aria-label="Add prompt"
          >
            <Plus size={12} />
            <span>Add prompt</span>
          </button>
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

function NumberField({
  label,
  ariaLabel,
  value,
  max,
  onChange,
}: {
  label: string;
  ariaLabel: string;
  value: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">
      <span>{label}</span>
      <input
        type="number"
        className="nodrag w-16 rounded border border-slate-200 bg-transparent px-1 py-0.5 text-xs text-slate-700 outline-none focus:ring-1 focus:ring-indigo-300 dark:border-slate-700 dark:text-slate-200"
        min={1}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={ariaLabel}
      />
    </label>
  );
}
