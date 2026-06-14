/**
 * ClassicMode "Preview" node view (Phase 5a W3 wave 2).
 *
 * A multi-modal display SINK — the standard ComfyUI "Preview". It reuses the
 * shared `ClassicNodeShell` (halo + typed handles) and renders an inline
 * framed display area below its two typed inputs (an image port + a text
 * port). It has ZERO outputs and is passive (see `classicDispatch.ts`), so it
 * never dispatches in the run cascade.
 *
 * MVP: the display area is a labelled placeholder — the cascade does not pipe
 * real data yet, so there is nothing to render but the "Preview" frame.
 */

import type { NodeProps } from '@xyflow/react';

import { classicNodeDefinitions } from '../registry';
import { ClassicNodeShell } from './ClassicNodeShell';
import { readRunData } from './classicNodeData';

const previewDef = classicNodeDefinitions.preview;

export function PreviewNodeView({ data, selected }: NodeProps) {
  const { run_status, run_error } = readRunData(data);
  return (
    <ClassicNodeShell
      def={previewDef}
      runStatus={run_status}
      runError={run_error}
      selected={Boolean(selected)}
    >
      <div
        data-testid="classic-node-preview-display"
        className="flex h-16 items-center justify-center rounded border border-dashed border-ink-700 bg-ink-950/40 text-[11px] text-ink-400"
      >
        Preview
      </div>
    </ClassicNodeShell>
  );
}
PreviewNodeView.displayName = 'PreviewNodeView';
