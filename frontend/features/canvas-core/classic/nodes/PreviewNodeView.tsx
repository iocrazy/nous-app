/**
 * ClassicMode "Preview" node view (Phase 5a W3 wave 2).
 *
 * A multi-modal display SINK — the standard ComfyUI "Preview". It reuses the
 * shared `ClassicNodeShell` (halo + typed handles) and renders an inline
 * framed display area below its two typed inputs (an image port + a text
 * port). It has ZERO outputs and is passive (see `classicDispatch.ts`), so it
 * never dispatches in the run cascade.
 *
 * The cascade folds its wired inputs into `data.preview_image` /
 * `data.preview_text` (P1-2, see cascade.ts passive branch + dataPiping
 * inputParamKey). When present, the frame renders the real piped image/text;
 * otherwise it falls back to the empty "Preview" placeholder.
 */

import type { NodeProps } from '@xyflow/react';

import { classicNodeDefinitions } from '../registry';
import { ClassicNodeShell } from './ClassicNodeShell';
import { readRunData } from './classicNodeData';

const previewDef = classicNodeDefinitions.preview;

function asDisplayString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null;
}

export function PreviewNodeView({ data, selected }: NodeProps) {
  const { run_status, run_error } = readRunData(data);
  const d = (data ?? {}) as Record<string, unknown>;
  const previewImage = asDisplayString(d.preview_image);
  const previewText = asDisplayString(d.preview_text);
  const hasContent = Boolean(previewImage || previewText);

  return (
    <ClassicNodeShell
      def={previewDef}
      runStatus={run_status}
      runError={run_error}
      selected={Boolean(selected)}
    >
      <div
        data-testid="classic-node-preview-display"
        className={
          hasContent
            ? 'flex flex-col gap-1.5 rounded border border-ink-700 bg-ink-950/40 p-1.5'
            : 'flex h-16 items-center justify-center rounded border border-dashed border-ink-700 bg-ink-950/40 text-[11px] text-ink-400'
        }
      >
        {!hasContent && 'Preview'}
        {previewImage && (
          <img
            data-testid="classic-node-preview-image"
            src={previewImage}
            alt="Preview"
            className="max-h-40 w-full rounded object-contain"
          />
        )}
        {previewText && (
          <div
            data-testid="classic-node-preview-text"
            className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] text-ink-200"
          >
            {previewText}
          </div>
        )}
      </div>
    </ClassicNodeShell>
  );
}
PreviewNodeView.displayName = 'PreviewNodeView';
