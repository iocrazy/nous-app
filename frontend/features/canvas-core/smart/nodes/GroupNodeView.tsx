// features/canvas-core/smart/nodes/GroupNodeView.tsx
//
// Group container, v2 (IC's smart-group): a translucent labelled region
// sitting UNDER its members (React Flow renders parents behind children)
// that also COLLECTS media — dropping a media node onto it absorbs the
// items into a thumbnail grid (see grouping.absorbMediaIntoGroup), and
// dropping FILES onto it uploads straight into the grid through the
// generated-media import route. A group holding media is a generation
// source: its right handle wires into prompts (promptInputs).

import { mediaSrc } from '../mediaUrl';
import { useCallback, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Loader2, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { downloadBlob, downloadName } from '../downloadMedia';
import { downloadCanvasAssetsZip } from '../../services/canvasGenerationService';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import {
  CANVAS_MEDIA_MAX_BYTES,
  importCanvasMedia,
  isImportableCanvasFile,
} from '../mediaImport';
import {
  GRID_CELL,
  GRID_MAX,
  arrangeGroupChildren,
  gridColsFor,
  ungroupNode,
} from '../grouping';
import { stitchImageItems } from '../stitchImages';
import type { GeneratedImageRef, GroupNodeData } from '../types';
import type { CanvasNode } from '../../types';
import { AttachedComposerPanel } from './AttachedComposerPanel';
import { GroupNodeToolbar } from './GroupNodeToolbar';
import { OutputLightbox, type LightboxItem } from './OutputLightbox';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';

export function GroupNodeView({ id, data, selected }: NodeProps) {
  const { t } = useTranslation();
  const { label, items, uploading } = data as unknown as GroupNodeData;
  const patch = useNodeDataPatch(id);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const memberCount = useCanvasCoreStore(
    (s) =>
      s.nodes.filter((n) => (n as { parentId?: string }).parentId === id).length,
  );
  const [dragOver, setDragOver] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  // Read-only: the label edit and the file drop both write (the drop also
  // POSTs an import), so both go. The thumbnail grid stays fully visible.
  const readOnly = useCanvasReadOnly();

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

  const imageItems: LightboxItem[] = (items ?? [])
    .filter((item) => item.kind !== 'video')
    .map((item) => ({ url: item.url, name: item.name }));

  const onArrange = useCallback(() => {
    const { nodes, setNodes } = useCanvasCoreStore.getState();
    setNodes(arrangeGroupChildren(nodes as CanvasNode[], id));
  }, [id]);

  const onUngroup = useCallback(() => {
    const { nodes, setNodes } = useCanvasCoreStore.getState();
    setNodes(ungroupNode(nodes as CanvasNode[], id));
  }, [id]);

  const onDownload = useCallback(() => {
    void (async () => {
      try {
        const zipName = `${(label || 'group').replace(/[^\w.-]+/g, '-')}-assets.zip`;
        const blob = await downloadCanvasAssetsZip(
          zipName,
          (items ?? []).map((it, i) => ({
            url: it.url,
            name: downloadName({ url: it.url, name: it.name }, i),
          })),
        );
        downloadBlob(blob, zipName);
      } catch (err) {
        console.error('[GroupNodeView] zip download failed:', err);
      }
    })();
  }, [items, label]);

  const onStitch = useCallback(() => {
    void (async () => {
      const store = useCanvasCoreStore.getState;
      const dataOf = () =>
        ((store().nodes.find((n) => (n as { id?: unknown }).id === id) as
          | { data?: GroupNodeData }
          | undefined)?.data ?? {}) as GroupNodeData;
      patch({ uploading: (dataOf().uploading ?? 0) + 1 });
      try {
        const blob = await stitchImageItems(imageItems);
        const file = new File([blob], 'stitched-grid.png', { type: 'image/png' });
        const item = await importCanvasMedia(file, canvasId, id);
        const current = dataOf();
        patch({
          items: [...(current.items ?? []), item],
          uploading: Math.max(0, (current.uploading ?? 1) - 1),
        });
      } catch (err) {
        console.error('[GroupNodeView] stitch failed:', err);
        const current = dataOf();
        patch({ uploading: Math.max(0, (current.uploading ?? 1) - 1) });
      }
    })();
  }, [canvasId, id, imageItems, patch]);

  const shown = (items ?? []).slice(0, GRID_MAX);
  const overflow = (items?.length ?? 0) - shown.length;
  const pending = Math.max(0, uploading ?? 0);
  const isEmpty = shown.length === 0 && pending === 0;
  // Adaptive column count (IC smartGroupThumbLayout): min 2, max 4, √n.
  const cols = gridColsFor(shown.length + pending);

  return (
    <div
      data-testid="smart-group-node"
      // IC's group-node: a frosted-glass card (solid hairline, not dashed) —
      // the dashed affordance moves INTO the empty drop-zone below.
      className={`group mh-group-node flex h-full w-full flex-col rounded-[var(--canvas-r-node)] border p-3 ${
        selected ? 'mh-node-selected border-canvas-line-strong' : 'border-canvas-line'
      } ${dragOver ? 'ring-2 ring-indigo-500/50' : ''}`}
      onDragOver={(e) => {
        // No drag-over highlight in a read-only session: a ring that
        // promises a drop we then refuse is the same lie as a lit button.
        if (!readOnly && e.dataTransfer.types.includes('Files')) {
          e.preventDefault();
          setDragOver(true);
        }
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        if (readOnly) return;
        if (!e.dataTransfer.files?.length) return;
        e.preventDefault();
        e.stopPropagation();
        setDragOver(false);
        void uploadFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <AttachedComposerPanel
        nodeId={id}
        inputUrls={imageItems.map((it) => it.url)}
        pinned={Boolean(selected)}
        readOnly={readOnly}
      />
      <GroupNodeToolbar
        imageCount={imageItems.length}
        memberCount={memberCount}
        onArrange={onArrange}
        onPreview={() => setLightboxIndex(0)}
        onStitch={onStitch}
        onDownload={onDownload}
        onUngroup={onUngroup}
        readOnly={readOnly}
        pinned={Boolean(selected)}
      />
      <input
        className="nodrag mb-1.5 w-32 shrink-0 bg-transparent text-[11px] font-bold uppercase tracking-[0.12em] text-canvas-muted outline-none placeholder:text-canvas-muted/60 focus:text-canvas-text read-only:opacity-80 read-only:cursor-default"
        value={label ?? ''}
        placeholder={t('canvas.groupNode.title', 'Group')}
        onChange={(e) => patch({ label: e.target.value })}
        aria-label="Group label"
        readOnly={readOnly}
      />
      {isEmpty ? (
        // IC's smart-group-empty: dashed drop-zone + "拖入图片自动收进分组".
        <div
          data-testid="group-empty"
          className="flex flex-1 flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-canvas-line bg-canvas-card/30 px-2 py-3 text-center"
        >
          <Plus size={14} className="text-canvas-muted" />
          {/* The empty group is nothing BUT its drop invitation, so the
              hint itself has to change — withdrawing the drop while still
              saying "drop images here" is the same broken promise. */}
          <span className="text-[10px] font-semibold leading-snug text-canvas-muted">
            {readOnly
              ? t('canvas.groupNode.readOnlyHint', 'Read-only — cannot add images')
              : t('canvas.groupNode.dropHint', 'Drop images to auto-collect')}
          </span>
        </div>
      ) : (
        <div
          data-testid="group-media-grid"
          className="grid flex-1 content-start gap-1.5"
          style={{ gridTemplateColumns: `repeat(${cols}, ${GRID_CELL}px)` }}
        >
          {shown.map((item, i) => (
            <div
              key={`${item.url}-${i}`}
              className="overflow-hidden rounded-md border border-canvas-line/60 bg-canvas-card"
              style={{ width: GRID_CELL, height: GRID_CELL }}
            >
              {item.kind === 'video' ? (
                <video src={mediaSrc(item.url)} preload="metadata" muted className="h-full w-full object-cover" />
              ) : (
                <img src={mediaSrc(item.url)} alt={item.name ?? ''} loading="lazy" className="h-full w-full object-cover" />
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
      {/* Collector handle (IC ⑦): image-bearing cards wire IN and their
          images absorb into the grid at connect time. */}
      <Handle type="target" position={Position.Left} />
      {/* Media source handle (group v2): a group with absorbed media wires
          into prompts as an i2i source. Always present — an empty group can
          be wired first and filled after. */}
      <Handle type="source" position={Position.Right} />
      {lightboxIndex !== null && imageItems.length > 0 && (
        <OutputLightbox
          items={imageItems}
          index={Math.min(lightboxIndex, imageItems.length - 1)}
          kind="image"
          onIndexChange={setLightboxIndex}
          onClose={() => setLightboxIndex(null)}
        />
      )}
    </div>
  );
}
