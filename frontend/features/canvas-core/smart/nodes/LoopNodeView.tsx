import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Plus, Square, Workflow, X } from 'lucide-react';

import type { LoopMode, LoopNodeData } from '../types';
import { LOOP_MODE_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { clampRoundStart, clampRounds } from '../loopVars';
import { startLoopRun } from '../loopRun';
import { useLoopRunStore } from '../loopRunStore';
import { useNodeDataPatch } from './useNodeDataPatch';

const MODE_OPTIONS: ReadonlyArray<{ value: LoopMode; label: string }> = [
  { value: 'serial', label: 'Serial' },
  { value: 'parallel', label: 'Parallel' },
  { value: 'batch', label: 'Batch' },
];

export function LoopNodeView({ id, data, selected }: NodeProps) {
  const { mode, label, rounds, round_start, prompts } = data as unknown as LoopNodeData;
  const patch = useNodeDataPatch(id);
  const running = useLoopRunStore((s) => Boolean(s.running[id]));
  const stopping = useLoopRunStore((s) => s.running[id]?.stopRequested ?? false);
  const tone = LOOP_MODE_TONE[mode];
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
      className={`mh-node ${tone} ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.loop }}
    >
      <Handle
        type="target"
        position={Position.Left}
      />
      <div className="mh-node-head">
        <div className="mh-node-title">Loop</div>
        <div className="flex items-center gap-1">
          <select
            className="nodrag mh-chip outline-none focus:ring-1 focus:ring-canvas-strong/40"
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
          {/* Infinite's loop-smart-run: Run ↔ Stop, disabled while stopping. */}
          <button
            type="button"
            className={`nodrag rounded p-1 disabled:cursor-not-allowed disabled:opacity-40 ${
              running
                ? 'text-rose-500 hover:text-rose-600'
                : 'text-indigo-500 hover:text-indigo-600'
            }`}
            onClick={() => {
              if (running) {
                useLoopRunStore.getState().requestStop(id);
                return;
              }
              startLoopRun(id).catch((err) => {
                console.error('[LoopNodeView] loop run failed:', err);
              });
            }}
            disabled={stopping}
            aria-label={running ? 'Stop loop' : 'Run loop'}
            title={running ? (stopping ? 'Stopping…' : 'Stop after this round') : 'Run all rounds'}
          >
            {running ? <Square size={11} /> : <Workflow size={11} />}
          </button>
        </div>
      </div>
      <div className="space-y-2 p-3">
        <input
          className="nodrag w-full bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:ring-1 focus:ring-canvas-strong/40 dark:text-slate-200"
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
                className="nodrag min-h-[34px] w-full resize-y rounded-full border border-canvas-line bg-transparent px-1.5 py-1 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:ring-1 focus:ring-canvas-strong/40  dark:text-slate-200"
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
        className="nodrag w-16 rounded-full border border-canvas-line bg-transparent px-1 py-0.5 text-xs text-slate-700 outline-none focus:ring-1 focus:ring-canvas-strong/40  dark:text-slate-200"
        min={1}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={ariaLabel}
      />
    </label>
  );
}
