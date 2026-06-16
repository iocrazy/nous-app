/**
 * ClassicMode "Text Join" node view (Phase 5a W3).
 *
 * A client-side TEXT CONCATENATION transform. It accepts two text inputs
 * (`text-a-in` / `text-b-in`) and emits one joined text output (`text-out`).
 * The cascade processes it as a `transform` dispatch (see `classicDispatch.ts`):
 * effective data is computed (piping applies), the join is recorded, and the
 * node is counted as `skipped` — no backend call is made.
 *
 * This view uses `ClassicNodeShell` (halo + typed handles) and `NodeConfigFields`
 * for the static `text_a`, `text_b`, and `separator` fields, and additionally
 * renders an inline PREVIEW of the join from the statically-entered values. The
 * preview reflects what the node will ACTUALLY produce when no wires are
 * connected; wired inputs will override it at cascade time (but the view only
 * sees stored data, not effective data).
 */

import type { NodeProps } from '@xyflow/react';

import { classicNodeDefinitions } from '../registry';
import { ClassicNodeShell } from './ClassicNodeShell';
import { NodeConfigFields } from './NodeConfigFields';
import { readRunData } from './classicNodeData';

const textJoinDef = classicNodeDefinitions.text_join;

/** Compute the inline join preview from stored data (static fallback values).
 *  Returns null when both parts are absent — no preview box is rendered. */
function computePreview(data: unknown): string | null {
  const obj = (data ?? {}) as Record<string, unknown>;
  const sep = typeof obj.separator === 'string' ? obj.separator : ' ';
  const parts = [obj.text_a, obj.text_b].filter(
    (v): v is string => typeof v === 'string' && v.length > 0,
  );
  return parts.length > 0 ? parts.join(sep) : null;
}

export function TextJoinNodeView({ id, data, selected }: NodeProps) {
  const { run_status, run_error } = readRunData(data);
  const preview = computePreview(data);

  return (
    <ClassicNodeShell
      def={textJoinDef}
      runStatus={run_status}
      runError={run_error}
      selected={Boolean(selected)}
    >
      {/* Static text_a / text_b / separator editors. These serve as FALLBACK
          values: when no wire is connected to an input, the cascade uses the
          stored data. A connected wire overrides the widget at cascade time
          (ComfyUI semantics). Disabled mid-run to avoid racing in-flight runs. */}
      <NodeConfigFields
        id={id}
        type={textJoinDef.type}
        data={data}
        disabled={run_status === 'running'}
      />
      {/* Inline join preview — shows what the static (non-piped) output will be.
          Hidden when neither text_a nor text_b has a value. */}
      {preview !== null && (
        <div
          className="mt-2 max-h-20 overflow-y-auto whitespace-pre-wrap break-words rounded border border-ink-700 bg-ink-950 p-2 text-[11px] leading-snug text-ink-200"
          data-testid="classic-node-text_join-preview"
        >
          {preview}
        </div>
      )}
    </ClassicNodeShell>
  );
}
TextJoinNodeView.displayName = 'TextJoinNodeView';
