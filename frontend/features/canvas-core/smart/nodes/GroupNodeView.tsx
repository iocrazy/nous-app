// features/canvas-core/smart/nodes/GroupNodeView.tsx
//
// Group container, v2 (IC's smart-group): a translucent labelled region
// sitting UNDER its members (React Flow renders parents behind children)
// that also COLLECTS media — dropping a media node onto it absorbs the
// items into a thumbnail grid (see grouping.absorbMediaIntoGroup), and
// dropping FILES onto it uploads straight into the grid through the
// generated-media import route. A group holding media is a generation
// source: its right handle wires into prompts (promptInputs).

import { useCallback, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Loader2 } from 'lucide-react';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import {
  CANVAS_MEDIA_MAX_BYTES,
  importCanvasMedia,
  isImportableCanvasFile,
} from '../mediaImport';
import { GRID_CELL, GRID_COLS, GRID_MAX } from '../grouping';
import type { GeneratedImageRef, GroupNodeData } from '../types';
import { useNodeDataPatch } from './useNodeDataPatch';

export function GroupNodeView({ id, data, selected }: NodeProps) {
  const { label, items, uploading } = data as unknown as GroupNodeData;
  const patch = useNodeDataPatch(id);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const [dragOver, setDragOver] = useState(false);

  const uploadFiles = useCallback(
    async (files: File[]) => {
      const importable = files.filter(
        (f) => isImportableCanvasFile(f) && f.size <= CANVAS_MEDIA_MAX_BYTES,
      );
      if (importable.length === 0) return;
      const store = useCanvasCoreStore.getState;
      const dataOf = () =>
        ((store().nodes.find((n) => (n as { id?: unknown }).id === id) as
          | { data?: GroupNodeData }
          | undefined)?.data ?? {}) as GroupNodeData;
      patch({ uploading: (dataOf().uploading ?? 0) + importable.length });
      for (const file of importable) {
        try {
          const item: GeneratedImageRef = await importCanvasMedia(file, canvasId, id);
          const current = dataOf();
          patch({
            items: [...(current.items ?? []), item],
            uploading: Math.max(0, (current.uploading ?? 1) - 1),
          });
        } catch (err) {
          console.error('[GroupNodeView] upload failed:', err);
          const current = dataOf();
          patch({ uploading: Math.max(0, (current.uploading ?? 1) - 1) });
        }
      }
    },
    [canvasId, id, patch],
  );

  const shown = (items ?? []).slice(0, GRID_MAX);
  const overflow = (items?.length ?? 0) - shown.length;
  const pending = Math.max(0, uploading ?? 0);

  return (
    <div
      data-testid="smart-group-node"
      className={`h-full w-full rounded-[var(--canvas-r-node)] border border-dashed ${
        selected ? 'mh-node-selected border-canvas-line-strong' : 'border-canvas-line'
      } ${dragOver ? 'ring-2 ring-indigo-500/50' : ''}`}
      style={{ background: 'color-mix(in srgb, var(--canvas-card) 42%, transparent)' }}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes('Files')) {
          e.preventDefault();
          setDragOver(true);
        }
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.files?.length) return;
        e.preventDefault();
        e.stopPropagation();
        setDragOver(false);
        void uploadFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <input
        className="nodrag absolute left-3 top-2 w-32 bg-transparent text-[11px] font-bold uppercase tracking-[0.12em] text-canvas-muted outline-none placeholder:text-canvas-muted/60 focus:text-canvas-text"
        value={label ?? ''}
        placeholder="Group"
        onChange={(e) => patch({ label: e.target.value })}
        aria-label="Group label"
      />
      {(shown.length > 0 || pending > 0) && (
        <div
          data-testid="group-media-grid"
          className="absolute left-3 top-8 flex flex-wrap gap-1.5"
          style={{ maxWidth: GRID_COLS * (GRID_CELL + 6) }}
        >
          {shown.map((item, i) => (
            <div
              key={`${item.url}-${i}`}
              className="overflow-hidden rounded-md border border-canvas-line/60 bg-canvas-card"
              style={{ width: GRID_CELL, height: GRID_CELL }}
            >
              {item.kind === 'video' ? (
                <video
                  src={item.url}
                  preload="metadata"
                  muted
                  className="h-full w-full object-cover"
                />
              ) : (
                <img
                  src={item.url}
                  alt={item.name ?? ''}
                  loading="lazy"
                  className="h-full w-full object-cover"
                />
              )}
            </div>
          ))}
          {Array.from({ length: pending }).map((_, i) => (
            <div
              key={`pending-${i}`}
              className="mh-loading-cell flex items-center justify-center rounded-md"
              style={{ width: GRID_CELL, height: GRID_CELL }}
            >
              <Loader2 size={12} className="animate-spin text-canvas-muted" />
            </div>
          ))}
          {overflow > 0 && (
            <div
              className="flex items-center justify-center rounded-md border border-canvas-line/60 text-[11px] font-medium text-canvas-muted"
              style={{ width: GRID_CELL, height: GRID_CELL }}
            >
              +{overflow}
            </div>
          )}
        </div>
      )}
      {/* Media source handle (group v2): a group with absorbed media wires
          into prompts as an i2i source. Always present — an empty group can
          be wired first and filled after. */}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
