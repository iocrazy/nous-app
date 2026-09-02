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
import { useCallback, useMemo, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Loader2, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useShallow } from 'zustand/react/shallow';

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
  groupPreviewItems,
  groupSummary,
  ungroupNode,
} from '../grouping';
import { stitchImageItems } from '../stitchImages';
import type { GeneratedImageRef, GroupNodeData } from '../types';
import type { CanvasNode } from '../../types';
import { AttachedComposerPanel } from './AttachedComposerPanel';
import { GroupNodeToolbar } from './GroupNodeToolbar';
import { useNodeReveal } from './useNodeReveal';
import { OutputLightbox, type LightboxItem } from './OutputLightbox';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';
import { NodeWidthGrip } from './NodeWidthGrip';

export function GroupNodeView({ id, data, selected }: NodeProps) {
  const { t } = useTranslation();
  const { label, items, uploading } = data as unknown as GroupNodeData;
  const patch = useNodeDataPatch(id);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  // Per-node selectors, never a subscription to `s.nodes` (Wave 1+2 Task 4):
  // that array is replaced on every drag frame, so subscribing to it
  // re-rendered this card whenever ANY node moved. `groupSummary` returns
  // three counts — `useShallow` holds the previous object while they match.
  const summary = useCanvasCoreStore(
    useShallow((s) => groupSummary(s.nodes as never, id)),
  );
  const memberCount = useCanvasCoreStore(
    (s) =>
      s.nodes.filter((n) => (n as { parentId?: string }).parentId === id).length,
  );
  const [dragOver, setDragOver] = useState(false);
  // IC 宫格拼接 options (rows×cols preset / gap / output long edge).
  const [stitchOpen, setStitchOpen] = useState(false);
  const [stitchCols, setStitchCols] = useState<number | undefined>(undefined);
  const [stitchGap, setStitchGap] = useState(0);
  const [stitchSize, setStitchSize] = useState(2048);
  // η3 grip: group size lives on node.style (persisted). The oversized
  // groups minted during the giant-node era are stuck at their baked-in
  // style — the grip lets the user drag them back down directly.
  // Primitive selectors — an object-returning selector re-renders forever
  // (zustand identity check), the same trap as the groupSummary incident.
  const groupW = useCanvasCoreStore(
    (s) =>
      ((s.nodes.find((n) => (n as { id?: unknown }).id === id) as
        | { style?: { width?: number } }
        | undefined)?.style?.width ?? 260),
  );
  const groupH = useCanvasCoreStore(
    (s) =>
      ((s.nodes.find((n) => (n as { id?: unknown }).id === id) as
        | { style?: { height?: number } }
        | undefined)?.style?.height ?? 180),
  );
  const resizeGroup = (w: number, h?: number) => {
    const { nodes, setNodes } = useCanvasCoreStore.getState();
    setNodes(
      nodes.map((n) => {
        if ((n as { id?: unknown }).id !== id) return n;
        const obj = n as Record<string, unknown>;
        const style = { ...((obj.style as object) ?? {}) } as Record<string, unknown>;
        style.width = w;
        if (h !== undefined) style.height = h;
        return { ...obj, style } as never;
      }),
    );
  };
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

  // Whole-group preview (IC): grid items + member images, deduped.
  // Selected as a STRING signature, not the array: its elements are fresh
  // objects on every call, so neither reference nor shallow equality would
  // hold and the card would re-render on every drag frame. Same shape as
  // `promptStatusSig` in `CanvasSurface.tsx`.
  const imageItemsSig = useCanvasCoreStore((s) =>
    JSON.stringify(groupPreviewItems(s.nodes as never, id)),
  );
  const imageItems: LightboxItem[] = useMemo(
    () => JSON.parse(imageItemsSig) as LightboxItem[],
    [imageItemsSig],
  );

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
  }, [items, label, stitchCols, stitchGap, stitchSize]);

  const onStitch = useCallback(() => {
    setStitchOpen(false);
    void (async () => {
      const store = useCanvasCoreStore.getState;
      const dataOf = () =>
        ((store().nodes.find((n) => (n as { id?: unknown }).id === id) as
          | { data?: GroupNodeData }
          | undefined)?.data ?? {}) as GroupNodeData;
      patch({ uploading: (dataOf().uploading ?? 0) + 1 });
      try {
        const blob = await stitchImageItems(imageItems, {
          cols: stitchCols,
          gap: stitchGap,
          outputLongEdge: stitchSize,
        });
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

  // Mount the floating toolbar only while the card is hovered / focused
  // (fluency T5) — see useNodeReveal.
  const { revealed, revealHandlers } = useNodeReveal();

  return (
    <div
      data-testid="smart-group-node"
      // IC's group-node: a frosted-glass card (solid hairline, not dashed) —
      // the dashed affordance moves INTO the empty drop-zone below.
      className={`group mh-group-node relative flex h-full w-full flex-col rounded-[var(--canvas-r-node)] border p-3 ${
        selected ? 'mh-node-selected border-canvas-line-strong' : 'border-canvas-line'
      } ${dragOver ? 'ring-2 ring-indigo-500/50' : ''}`}
      {...revealHandlers}
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
        onStitch={() => setStitchOpen((v) => !v)}
        onDownload={onDownload}
        onUngroup={onUngroup}
        readOnly={readOnly}
        pinned={Boolean(selected)}
        hovered={revealed}
      />
      <input
        className="nodrag mb-1.5 w-32 shrink-0 bg-transparent text-[11px] font-bold uppercase tracking-[0.12em] text-canvas-muted outline-none placeholder:text-canvas-muted/60 focus:text-canvas-text read-only:opacity-80 read-only:cursor-default"
        value={label ?? ''}
        placeholder={t('canvas.groupNode.title', 'Group')}
        onChange={(e) => patch({ label: e.target.value })}
        aria-label="Group label"
        readOnly={readOnly}
      />
      {(summary.prompts > 0 || summary.loops > 0 || summary.images > 0) && (
        <div
          data-testid="group-summary"
          className="mb-1 text-[9px] font-semibold text-canvas-muted"
        >
          {[
            summary.prompts > 0 ? `${summary.prompts} prompts` : null,
            summary.images > 0 ? `${summary.images} images` : null,
            summary.loops > 0 ? `${summary.loops} loops` : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </div>
      )}
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
      {stitchOpen && !readOnly && (
        <div
          data-testid="stitch-options"
          className="canvas-island absolute -top-2 left-1/2 z-20 w-64 -translate-x-1/2 -translate-y-full rounded-xl p-2 text-xs"
        >
          <div className="mb-1 font-bold text-canvas-text">Stitch</div>
          <div className="mb-1.5 flex flex-wrap items-center gap-1">
            <span className="text-[10px] text-canvas-muted">Grid</span>
            {[undefined, 1, 2, 3, 4].map((c) => (
              <button
                key={c ?? 'auto'}
                type="button"
                data-testid={`stitch-cols-${c ?? 'auto'}`}
                onClick={() => setStitchCols(c)}
                className={`nodrag rounded-full border px-2 py-0.5 text-[10px] ${
                  stitchCols === c
                    ? 'border-canvas-strong bg-canvas-strong text-canvas-card'
                    : 'border-canvas-line text-canvas-text'
                }`}
              >
                {c ? `${c} col` : 'Auto'}
              </button>
            ))}
          </div>
          <label className="mb-1.5 flex items-center gap-1.5 text-[10px] text-canvas-muted">
            Gap
            <input
              type="range"
              min={0}
              max={240}
              value={stitchGap}
              onChange={(e) => setStitchGap(Number(e.target.value))}
              aria-label="Stitch gap"
              className="nodrag flex-1"
            />
            <span className="w-8 text-right font-semibold text-canvas-text">
              {stitchGap}
            </span>
          </label>
          <div className="mb-1.5 flex items-center gap-1">
            <span className="text-[10px] text-canvas-muted">Output</span>
            {[1024, 2048, 4096].map((sz) => (
              <button
                key={sz}
                type="button"
                data-testid={`stitch-size-${sz}`}
                onClick={() => setStitchSize(sz)}
                className={`nodrag rounded-full border px-2 py-0.5 text-[10px] ${
                  stitchSize === sz
                    ? 'border-canvas-strong bg-canvas-strong text-canvas-card'
                    : 'border-canvas-line text-canvas-text'
                }`}
              >
                {sz === 1024 ? '1K' : sz === 2048 ? '2K' : '4K'}
              </button>
            ))}
          </div>
          <button
            type="button"
            data-testid="stitch-apply"
            onClick={onStitch}
            className="nodrag w-full rounded-full bg-canvas-strong px-3 py-1 text-xs font-bold text-canvas-card"
          >
            Stitch {imageItems.length} images
          </button>
        </div>
      )}
      {!readOnly && (
        <NodeWidthGrip
          value={groupW}
          heightValue={groupH}
          min={200}
          max={2000}
          minHeight={140}
          onChange={(w, h) => resizeGroup(w, h)}
        />
      )}
    </div>
  );
}
