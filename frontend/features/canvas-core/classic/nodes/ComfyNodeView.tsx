/**
 * ClassicMode `comfy` node view (Phase 5a B4).
 *
 * A ComfyUI-graph node. Beyond the shared halo + typed ports it adds two
 * run-time affordances while the node is running:
 *   - a live ELAPSED-SECONDS readout computed from `run_started_at`,
 *     ticking once per second (see useElapsedSeconds), and
 *   - a CANCEL button that aborts the node's in-flight fetch via the
 *     shared per-node AbortController registry and flips the node back to
 *     `idle`.
 *
 * MVP NOTE: Cancel does NOT yet tell the backend to stop the ComfyUI run
 * — it only aborts the client-side fetch and resets local UI state. Real
 * backend cancellation is a later slice (the run dispatch isn't wired in
 * the MVP either). There is no `cancelled` run_status, so we use `idle`.
 */

import type { NodeProps } from '@xyflow/react';

import { getClassicNodeDefinition } from '../registry';
import { abortNode } from '../abortRegistry';
import { useNodeDataPatch } from '../../smart/nodes/useNodeDataPatch';
import { ClassicNodeShell } from './ClassicNodeShell';
import { readRunData } from './classicNodeData';
import { formatElapsed, useElapsedSeconds } from './elapsed';

const COMFY_DEF = getClassicNodeDefinition('comfy')!;

export function ComfyNodeView({ id, data, selected }: NodeProps) {
  const { run_status, run_started_at, run_error } = readRunData(data);
  const patch = useNodeDataPatch(id);
  const running = run_status === 'running';
  const elapsed = useElapsedSeconds(run_started_at, running);

  // MVP cancellation: abort the client-side fetch (if any) and reset the
  // node to idle. No backend cancel call yet — see file header.
  const onCancel = () => {
    abortNode(id);
    patch({ run_status: 'idle', run_started_at: null });
  };

  return (
    <ClassicNodeShell
      def={COMFY_DEF}
      runStatus={run_status}
      runError={run_error}
      selected={Boolean(selected)}
    >
      {running && (
        <div className="flex items-center justify-between gap-2">
          <span
            className="font-mono text-[11px] tabular-nums text-ink-200"
            data-testid="comfy-elapsed"
          >
            {formatElapsed(elapsed)}
          </span>
          <button
            type="button"
            className="nodrag rounded border border-ink-600 bg-ink-800 px-2 py-0.5 text-[11px] text-ink-100 hover:bg-ink-700"
            onClick={onCancel}
            data-testid="comfy-cancel"
          >
            Cancel
          </button>
        </div>
      )}
    </ClassicNodeShell>
  );
}
