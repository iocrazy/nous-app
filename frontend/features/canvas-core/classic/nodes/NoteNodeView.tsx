/**
 * ClassicMode "Note" node view (Phase 5a W3).
 *
 * A pure-UI sticky annotation — the standard ComfyUI "Note". Unlike every
 * other classic node it has ZERO typed ports, so it renders NO React Flow
 * <Handle>s and never participates in a wire or the run cascade (it is
 * passive — see `classicDispatch.ts`). MVP: render the note text only; no
 * inline editing yet.
 */

import type { NodeProps } from '@xyflow/react';

/** Read the displayable note text off an untyped node `data` blob, preferring
 *  an explicit `text`, then falling back to `label`. */
function readNoteText(data: unknown): string {
  const obj = (data ?? {}) as Record<string, unknown>;
  for (const key of ['text', 'label']) {
    const v = obj[key];
    if (typeof v === 'string' && v.trim()) return v;
  }
  return '';
}

export function NoteNodeView({ data }: NodeProps) {
  const text = readNoteText(data);
  return (
    <div
      data-testid="classic-node-note"
      className="min-w-[160px] max-w-[240px] rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-ink-100 shadow"
    >
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-400">
        Note
      </div>
      <div className="whitespace-pre-wrap break-words text-xs text-ink-200">
        {text}
      </div>
    </div>
  );
}
NoteNodeView.displayName = 'NoteNodeView';
