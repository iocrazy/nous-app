/**
 * Shared ClassicMode runnable-op node view (Phase 5a path B).
 *
 * Generalises the original ComfyNodeView so every long-running AI-op node
 * (`comfy` / `image_gen` / `video_gen`) shares ONE view with the same
 * affordances while running:
 *   - a live ELAPSED-SECONDS readout computed from `run_started_at`,
 *     ticking once per second (see useElapsedSeconds), and
 *   - a CANCEL button that aborts the node's in-flight fetch via the shared
 *     per-node AbortController registry and flips the node back to `idle`.
 *
 * On success it renders a small `ink-*` framed RESULT indicator off the
 * structured op output the cascade patched into `data.run_result`
 * (`image_url` / `video_url` / `thumbnail_url`) — MVP inline preview.
 *
 * MVP NOTE: Cancel does NOT yet tell the backend to stop the run — it only
 * aborts the client-side fetch and resets local UI state. There is no
 * `cancelled` run_status, so we use `idle`.
 */

import type { NodeProps } from '@xyflow/react';

import type { ClassicNodeDefinition } from '../registry';
import { abortNode } from '../abortRegistry';
import { useNodeDataPatch } from '../../smart/nodes/useNodeDataPatch';
import { ClassicNodeShell } from './ClassicNodeShell';
import { NodeConfigFields } from './NodeConfigFields';
import { readRunData } from './classicNodeData';
import { formatElapsed, useElapsedSeconds } from './elapsed';

/** Pull the first usable preview URL off the structured op output. */
function readResultUrl(data: unknown): string | null {
  const obj = (data ?? {}) as Record<string, unknown>;
  const result = obj.run_result;
  if (!result || typeof result !== 'object') return null;
  const r = result as Record<string, unknown>;
  for (const key of ['thumbnail_url', 'image_url', 'video_url']) {
    const v = r[key];
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return null;
}

/**
 * Build a runnable-op node view bound to a registry def. `testidPrefix`
 * keys the elapsed / cancel / result test ids (e.g. `comfy-elapsed`).
 */
export function makeRunnableOpNodeView(
  def: ClassicNodeDefinition,
  testidPrefix: string,
) {
  function RunnableOpNodeView({ id, data, selected }: NodeProps) {
    const { run_status, run_started_at, run_error } = readRunData(data);
    const patch = useNodeDataPatch(id);
    const running = run_status === 'running';
    const elapsed = useElapsedSeconds(run_started_at, running);
    const resultUrl = run_status === 'succeeded' ? readResultUrl(data) : null;

    // MVP cancellation: abort the client-side fetch (if any) and reset the
    // node to idle. No backend cancel call yet — see file header.
    const onCancel = () => {
      abortNode(id);
      patch({ run_status: 'idle', run_started_at: null });
    };

    return (
      <ClassicNodeShell
        def={def}
        runStatus={run_status}
        runError={run_error}
        selected={Boolean(selected)}
      >
        {/* Inline config editors — write run params straight to node.data
            (the keys the backend _extract_*_params read). Disabled mid-run so
            edits can't race the in-flight request. */}
        <NodeConfigFields id={id} type={def.type} data={data} disabled={running} />
        {running && (
          <div className="flex items-center justify-between gap-2">
            <span
              className="font-mono text-[11px] tabular-nums text-ink-200"
              data-testid={`${testidPrefix}-elapsed`}
            >
              {formatElapsed(elapsed)}
            </span>
            <button
              type="button"
              className="nodrag rounded border border-ink-600 bg-ink-800 px-2 py-0.5 text-[11px] text-ink-100 hover:bg-ink-700"
              onClick={onCancel}
              data-testid={`${testidPrefix}-cancel`}
            >
              Cancel
            </button>
          </div>
        )}
        {resultUrl && (
          <div
            className="mt-1 overflow-hidden rounded border border-ink-700 bg-ink-950"
            data-testid={`${testidPrefix}-result`}
          >
            <img
              src={resultUrl}
              alt={`${def.label} result`}
              className="h-16 w-full object-cover"
            />
          </div>
        )}
      </ClassicNodeShell>
    );
  }
  RunnableOpNodeView.displayName = `RunnableOpNodeView(${def.type})`;
  return RunnableOpNodeView;
}
