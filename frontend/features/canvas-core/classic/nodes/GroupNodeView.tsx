/**
 * ClassicMode "Group" node view (Phase 5a W3 wave 2).
 *
 * A pure-UI visual container/frame — the standard ComfyUI "Group". Like
 * `note` it has ZERO typed ports, so it renders NO React Flow <Handle>s and
 * never participates in a wire or the run cascade (it is passive — see
 * `classicDispatch.ts`). Visually it reads as a translucent dashed container
 * (distinct from the solid `note` sticky). MVP: render its label only.
 */

import type { NodeProps } from '@xyflow/react';

/** Read the displayable group label off an untyped node `data` blob,
 *  preferring an explicit `label`, then `title`, else a static fallback. */
function readGroupLabel(data: unknown): string {
  const obj = (data ?? {}) as Record<string, unknown>;
  for (const key of ['label', 'title']) {
    const v = obj[key];
    if (typeof v === 'string' && v.trim()) return v;
  }
  return 'Group';
}

export function GroupNodeView({ data }: NodeProps) {
  const label = readGroupLabel(data);
  return (
    <div
      data-testid="classic-node-group"
      className="min-h-[120px] min-w-[200px] rounded-lg border-2 border-dashed border-ink-600 bg-ink-800/30 px-3 py-2 text-ink-200 shadow-inner"
    >
      <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">
        {label}
      </div>
    </div>
  );
}
GroupNodeView.displayName = 'GroupNodeView';
