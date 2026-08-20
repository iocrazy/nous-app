import { mediaSrc } from '../mediaUrl';
import { Handle, NodeResizeControl, Position, type NodeProps } from '@xyflow/react';

import { NodeDeleteButton } from './NodeDeleteButton';
import { useCallback, useState } from 'react';

import { getResourceFileUrl } from '../../../../services/resourceService';
import { getSupabaseClient } from '../../../../supabaseClient';
import { UnifiedImageEditor, type EditorMode } from '../../editor/UnifiedImageEditor';
import { bakeAnnotations, bakeResize } from '../../editor/imageBake';
import { importCanvasMedia } from '../mediaImport';
import type { PaintShape } from '../../editor/PaintTool';
import { type GridLines } from '../../editor/gridMath';
import { strokesToMaskPngBase64 } from '../../editor/maskExport';
import { type MaskStroke } from '../../editor/maskMath';
import { type OutpaintPadding } from '../../editor/outpaintMath';
import { FULL_REGION, type CropRegion } from '../../editor/types';
import {
  deriveCrop,
  deriveGrid,
  deriveMaskCutout,
  deriveOutpaint,
} from '../../services/canvasService';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { createOutputNode } from '../factories';
import { requeryRecoverTask } from '../genResume';
import { latestHistoryImageUrl } from '../outputHistory';
import { resolveSourceUrls } from '../promptInputs';
import { promptIdForOutput, regenerateForOutput } from '../regenerate';
import { regenKey, useRegenStore } from '../regenStore';
import type { OutputNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { createMediaNodeFromFiles } from '../dropCreate';
import { OutputLightbox, type LightboxItem } from './OutputLightbox';
import { AttachedComposerPanel } from './AttachedComposerPanel';
import { OutputNodeToolbar } from './OutputNodeToolbar';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';


const KIND_LABEL: Record<OutputNodeData['kind'], string> = {
  text: 'Text',
  image: 'Image',
  video: 'Video',
  audio: 'Audio',
};

/** Layout for tile nodes spawned by a grid split: the grid starts one
 *  gap to the right of the source node and mirrors the tiles'
 *  row/col arrangement. */
const TILE_LAYOUT_GAP_X = 48;
const TILE_LAYOUT_STEP_Y = 220;

/** Build the served-file URL for a freshly-derived resource. The crop
 *  endpoint returns a resource row but no URL — the front-end composes
 *  it the same way ResourceCard does. */
async function buildPreviewUrl(resourceId: string): Promise<string> {
  try {
    const supabase = getSupabaseClient();
    const { data } = await supabase.auth.getSession();
    return getResourceFileUrl(resourceId, data.session?.access_token);
  } catch {
    // If we somehow can't read the session, fall back to the unsigned
    // URL — the <img> request will still carry the cookie auth if any.
    return getResourceFileUrl(resourceId);
  }
}

export function OutputNodeView({ id, data, selected }: NodeProps) {
  const {
    kind,
    resource_id,
    preview_text,
    preview_url,
    crop_region,
    images,
    history_for,
    gen_pending = 0,
    gen_failed = 0,
    gen_recover = [],
  } = data as unknown as OutputNodeData;
  const patchData = useNodeDataPatch(id);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<EditorMode | null>(null);
  const [pixCommitting, setPixCommitting] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [gridOpen, setGridOpen] = useState(false);
  const [gridCommitting, setGridCommitting] = useState(false);
  const [gridError, setGridError] = useState<string | null>(null);
  const [maskOpen, setMaskOpen] = useState(false);
  const [maskCommitting, setMaskCommitting] = useState(false);
  const [maskError, setMaskError] = useState<string | null>(null);
  const [outpaintOpen, setOutpaintOpen] = useState(false);
  const [outpaintCommitting, setOutpaintCommitting] = useState(false);
  const [outpaintError, setOutpaintError] = useState<string | null>(null);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const regenerating = useRegenStore((s) => !!s.running[regenKey(canvasId, id)]);
  // Read-only: Rerun re-dispatches the source prompt, and Crop / Expand /
  // Mask / Split each derive a NEW resource server-side and drop fresh
  // nodes on the canvas — all writes. The recover cell's "Check Result"
  // patches this node too. Preview, Download and the lightbox are reads
  // and stay available; the editing chips stay VISIBLE but disabled, so
  // the node keeps telling a viewer what it can do once they have access
  // (same call the command palette made in #1828).
  const readOnly = useCanvasReadOnly();

  const lightboxItems: LightboxItem[] =
    images && images.length > 0
      ? images.map((img) => ({ url: img.url, name: img.name }))
      : kind !== 'text' && preview_url
        ? [{ url: preview_url, name: preview_text || undefined }]
        : [];

  /** Open the lightbox at an item (P2-5 — double-click, no delay). Single
   *  click now selects the node (Infinite parity); the 250ms click/dblclick
   *  disambiguation timer is gone since crop moved to a header chip. */
  const openLightbox = useCallback(
    (index: number) => {
      if (lightboxItems.length === 0) return;
      setLightboxIndex(index);
    },
    [lightboxItems.length],
  );

  // Brush / Resize (unified editor's client-side modes): bake → import as
  // a NEW generated-media item appended to this node (non-destructive).
  const handleBrushCommit = useCallback(
    (shapes: PaintShape[]) => {
      if (!preview_url) return;
      void (async () => {
        try {
          setPixCommitting(true);
          const blob = await bakeAnnotations(preview_url, shapes);
          const file = new File([blob], 'brush.png', { type: 'image/png' });
          const item = await importCanvasMedia(file, canvasId, id);
          patchData({
            images: [
              ...((images as Array<{ url: string }>) ?? []),
              { url: item.url, kind: 'image', name: 'brush.png' },
            ],
          });
          setEditorMode(null);
        } catch (err) {
          console.error('[OutputNodeView] brush bake failed:', err);
          setCommitError(err instanceof Error ? err.message : 'Brush failed');
        } finally {
          setPixCommitting(false);
        }
      })();
    },
    [preview_url, canvasId, id, images, patchData],
  );
  const handleResizeCommit = useCallback(
    (scale: number) => {
      if (!preview_url) return;
      void (async () => {
        try {
          setPixCommitting(true);
          const blob = await bakeResize(preview_url, scale);
          const file = new File([blob], 'resized.png', { type: 'image/png' });
          const item = await importCanvasMedia(file, canvasId, id);
          patchData({
            images: [
              ...((images as Array<{ url: string }>) ?? []),
              { url: item.url, kind: 'image', name: 'resized.png' },
            ],
          });
          setEditorMode(null);
        } catch (err) {
          console.error('[OutputNodeView] resize bake failed:', err);
          setCommitError(err instanceof Error ? err.message : 'Resize failed');
        } finally {
          setPixCommitting(false);
        }
      })();
    },
    [preview_url, canvasId, id, images, patchData],
  );

  const canRegenerate = !!promptIdForOutput(id);
  const onRegenerate = useCallback(() => {
    void regenerateForOutput(id);
  }, [id]);

  const canCrop = kind === 'image' && !!preview_url;
  // Grid split has no local fallback — every tile is derived
  // server-side, so a persisted source resource is required.
  const canSplit = canCrop && !!resource_id;

  const openEditor = useCallback(() => {
    if (!canCrop) return;
    setCommitError(null);
    setEditorMode('crop');
  }, [canCrop]);

  const closeEditor = useCallback(() => {
    setEditorOpen(false);
        setEditorMode(null);
    setCommitError(null);
  }, []);

  const handleCommit = useCallback(
    async (region: CropRegion) => {
      // Fallback path: no resource_id means the image was supplied
      // ad-hoc (no backend record). Persist the region locally so the
      // visual stays correct; nothing to derive against.
      if (!resource_id) {
        patchData({ crop_region: region });
        setEditorOpen(false);
        setEditorMode(null);
        return;
      }
      try {
        setCommitting(true);
        setCommitError(null);
        const result = await deriveCrop(resource_id, region);
        const newId = String(result.id);
        const newUrl = await buildPreviewUrl(newId);
        patchData({
          resource_id: newId,
          preview_url: newUrl,
          // The new resource IS the cropped image — clear the in-node
          // crop so a second crop starts from a clean rectangle.
          crop_region: null,
        });
        setEditorOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Failed to derive crop';
        setCommitError(message);
        // Leave the modal open so the user can retry or cancel.
      } finally {
        setCommitting(false);
      }
    },
    [resource_id, patchData],
  );

  const openOutpaintEditor = useCallback(() => {
    if (!canSplit) return;
    setOutpaintError(null);
    setEditorMode('outpaint');
  }, [canSplit]);

  const closeOutpaintEditor = useCallback(() => {
    setOutpaintOpen(false);
        setEditorMode(null);
    setOutpaintError(null);
  }, []);

  const handleOutpaintCommit = useCallback(
    async (padding: OutpaintPadding, prompt: string) => {
      if (!resource_id) return;
      try {
        setOutpaintCommitting(true);
        setOutpaintError(null);
        const result = await deriveOutpaint(resource_id, padding, {
          prompt: prompt || undefined,
        });
        const newId = String(result.id);
        const newUrl = await buildPreviewUrl(newId);
        // Spawn the extended image as a fresh node beside the source —
        // same pattern as the mask cutout.
        const store = useCanvasCoreStore.getState();
        const self = store.nodes.find(
          (node) => (node as { id?: string }).id === id,
        ) as { position?: { x: number; y: number } } | undefined;
        const base = self?.position ?? { x: 0, y: 0 };
        const extendedNode = createOutputNode(
          {
            kind: 'image',
            resource_id: newId,
            preview_text: String(result.filename ?? ''),
            preview_url: newUrl,
          },
          {
            position: {
              x: base.x + SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X,
              y: base.y,
            },
          },
        );
        store.setNodes([...store.nodes, extendedNode]);
        setOutpaintOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Failed to extend canvas';
        setOutpaintError(message);
        // Leave the modal open so the user can retry or cancel.
      } finally {
        setOutpaintCommitting(false);
      }
    },
    [resource_id, id],
  );

  const openMaskEditor = useCallback(() => {
    if (!canSplit) return;
    setMaskError(null);
    setEditorMode('mask');
  }, [canSplit]);

  const closeMaskEditor = useCallback(() => {
    setMaskOpen(false);
        setEditorMode(null);
    setMaskError(null);
  }, []);

  const handleMaskCommit = useCallback(
    async (
      strokes: MaskStroke[],
      size: { width: number; height: number },
    ) => {
      if (!resource_id) return;
      try {
        setMaskCommitting(true);
        setMaskError(null);
        const maskB64 = strokesToMaskPngBase64(strokes, size.width, size.height);
        const result = await deriveMaskCutout(resource_id, maskB64);
        const newId = String(result.id);
        const newUrl = await buildPreviewUrl(newId);
        // Spawn the cutout as a fresh node beside the source (the
        // source stays — unlike crop, a cutout is a new artifact, not
        // a replacement).
        const store = useCanvasCoreStore.getState();
        const self = store.nodes.find(
          (node) => (node as { id?: string }).id === id,
        ) as { position?: { x: number; y: number } } | undefined;
        const base = self?.position ?? { x: 0, y: 0 };
        const cutoutNode = createOutputNode(
          {
            kind: 'image',
            resource_id: newId,
            preview_text: String(result.filename ?? ''),
            preview_url: newUrl,
          },
          {
            position: {
              x: base.x + SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X,
              y: base.y,
            },
          },
        );
        store.setNodes([...store.nodes, cutoutNode]);
        setMaskOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Failed to cut out region';
        setMaskError(message);
        // Leave the modal open so the user can retry or cancel.
      } finally {
        setMaskCommitting(false);
      }
    },
    [resource_id, id],
  );

  const openGridEditor = useCallback(() => {
    if (!canSplit) return;
    setGridError(null);
    setEditorMode('split');
  }, [canSplit]);

  const closeGridEditor = useCallback(() => {
    setGridOpen(false);
        setEditorMode(null);
    setGridError(null);
  }, []);

  const handleGridCommit = useCallback(
    async (lines: GridLines) => {
      if (!resource_id) return;
      try {
        setGridCommitting(true);
        setGridError(null);
        const result = await deriveGrid(resource_id, lines);
        const urls = await Promise.all(
          result.tiles.map((tile) => buildPreviewUrl(String(tile.resource.id))),
        );
        // Spawn one output node per tile, mirroring the grid's
        // row/col arrangement to the right of the source node. The
        // source node itself is left untouched.
        const store = useCanvasCoreStore.getState();
        const self = store.nodes.find(
          (node) => (node as { id?: string }).id === id,
        ) as { position?: { x: number; y: number } } | undefined;
        const base = self?.position ?? { x: 0, y: 0 };
        const startX =
          base.x + SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X;
        const tileNodes = result.tiles.map((tile, index) =>
          createOutputNode(
            {
              kind: 'image',
              resource_id: String(tile.resource.id),
              preview_text: String(tile.resource.filename ?? ''),
              preview_url: urls[index],
            },
            {
              position: {
                x:
                  startX +
                  tile.col *
                    (SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X),
                y: base.y + tile.row * TILE_LAYOUT_STEP_Y,
              },
            },
          ),
        );
        store.setNodes([...store.nodes, ...tileNodes]);
        setGridOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Failed to split image';
        setGridError(message);
        // Leave the modal open so the user can retry or cancel.
      } finally {
        setGridCommitting(false);
      }
    },
    [resource_id, id],
  );

  return (
    <div
      data-testid="smart-output-node"
      className={`group mh-node relative border-canvas-line ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: '100%', minWidth: SMART_NODE_DEFAULT_WIDTH.output }}
    >
      {/* IC node-resize-handle: drag the right edge to widen the card
          (media grids reflow; height stays content-driven). RF applies the
          resized width to the node wrapper — the root div tracks it via
          width:100% with the legacy default as its floor. */}
      {!readOnly && (
        <NodeResizeControl
          position="right"
          minWidth={SMART_NODE_DEFAULT_WIDTH.output}
          maxWidth={900}
          style={{ background: 'transparent', border: 'none', width: 8, cursor: 'ew-resize' }}
        />
      )}
      <Handle
        type="target"
        position={Position.Left}
      />
      <NodeDeleteButton nodeId={id} readOnly={readOnly} />
      {/* Floating toolbar (P2-3): pinned while selected, hover-revealed
          otherwise. Preview bypasses the 250ms crop-disambiguation delay. */}
      <AttachedComposerPanel
        nodeId={id}
        inputUrls={lightboxItems.map((it) => it.url)}
        pinned={Boolean(selected)}
        readOnly={readOnly}
      />
      {lightboxItems.length > 0 && (
        <OutputNodeToolbar
          items={lightboxItems}
          pinned={selected}
          onPreview={() => openLightbox(0)}
          onCrop={canCrop ? openEditor : undefined}
          onExpand={canSplit ? openOutpaintEditor : undefined}
          onMask={canSplit ? openMaskEditor : undefined}
          onBrush={() => setEditorMode('brush')}
          onSplit={canSplit ? openGridEditor : undefined}
          onRerun={canRegenerate ? onRegenerate : undefined}
          rerunning={regenerating}
          readOnly={readOnly}
        />
      )}
      <div className="mh-node-head">
        <div className="mh-node-title">
          {history_for ? 'History · ' : ''}
          Output · {KIND_LABEL[kind]}
        </div>
        <div className="flex items-center gap-2">
          {canRegenerate && (
            <button
              type="button"
              data-testid="regenerate-open"
              onClick={onRegenerate}
              disabled={regenerating || readOnly}
              title="Re-run the source prompt"
              className="mh-chip"
            >
              {regenerating ? 'Rerunning…' : 'Rerun'}
            </button>
          )}
          {canCrop && (
            /* Crop moved to a header chip (P2-5): double-click now opens the
               lightbox, so crop joins Expand/Mask/Split as an explicit
               editing affordance instead of the old double-click gesture. */
            <button
              type="button"
              data-testid="crop-open"
              onClick={openEditor}
              disabled={readOnly}
              title="Crop the image"
              className="mh-chip disabled:cursor-not-allowed disabled:opacity-50"
            >
              Crop
            </button>
          )}
          {canSplit && (
            <button
              type="button"
              data-testid="outpaint-open"
              onClick={openOutpaintEditor}
              disabled={readOnly}
              title="Extend the canvas beyond the image"
              className="mh-chip disabled:cursor-not-allowed disabled:opacity-50"
            >
              Expand
            </button>
          )}
          {canSplit && (
            <button
              type="button"
              data-testid="mask-cutout-open"
              onClick={openMaskEditor}
              disabled={readOnly}
              title="Paint a region to cut out"
              className="mh-chip disabled:cursor-not-allowed disabled:opacity-50"
            >
              Mask
            </button>
          )}
          {canSplit && (
            <button
              type="button"
              data-testid="grid-split-open"
              onClick={openGridEditor}
              disabled={readOnly}
              title="Split into a grid of tiles"
              className="mh-chip disabled:cursor-not-allowed disabled:opacity-50"
            >
              Split
            </button>
          )}
          {crop_region && (
            <div
              data-testid="crop-region-badge"
              className="text-[10px] uppercase tracking-wider text-canvas-strong"
              title="Crop applied"
            >
              Cropped
            </div>
          )}
          {resource_id && (
            <div className="text-[10px] uppercase tracking-wider text-emerald-500">
              Saved
            </div>
          )}
        </div>
      </div>
      <div
        data-testid="smart-output-body"
        // Double-click: images jump straight into the rich editor (IC
        // dblclick → imageEditModal); video/text keep the lightbox. Single
        // click falls through to React Flow node selection.
        onDoubleClick={() =>
          kind === 'image' && preview_url && !readOnly
            ? setEditorMode('preview')
            : openLightbox(0)
        }
        className={`p-3 ${lightboxItems.length > 0 ? 'cursor-zoom-in' : ''}`}
        title={lightboxItems.length > 0 ? 'Double-click to preview' : undefined}
      >
        {gen_pending > 0 || (images?.length ?? 0) + gen_pending > 1 ? (
          /* Multi-result grid (G4-F2) + in-flight shimmer cells (P0-3):
             the slot shows WHERE results land the moment the run is
             dispatched; each finished item replaces a cell as it arrives. */
          <div className="grid grid-cols-2 gap-1" data-testid="output-images-grid">
            {(images ?? []).map((img, i) => (
              <img
                key={`${img.url}-${i}`}
                src={mediaSrc(img.url)}
                alt={img.name || `Generated ${i + 1}`}
                draggable={false}
                onDoubleClick={(e) => {
                  // Don't let the body's dblclick (opens index 0) override
                  // this grid image's own index.
                  e.stopPropagation();
                  openLightbox(i);
                }}
                className="block w-full cursor-zoom-in rounded object-contain"
              />
            ))}
            {Array.from({ length: gen_pending }, (_, i) => (
              <div
                key={`pending-${i}`}
                data-testid="output-pending-cell"
                aria-label="Generating"
                className="mh-loading-cell aspect-square w-full rounded"
              />
            ))}
          </div>
        ) : kind === 'image' && (preview_url || images?.[0]?.url) ? (
          <img
            src={mediaSrc(preview_url || images?.[0]?.url)}
            alt={preview_text || 'Output preview'}
            draggable={false}
            onDoubleClick={(e) => {
              e.stopPropagation();
              if (!readOnly) setEditorMode('preview');
              else openLightbox(0);
            }}
            className="block w-full cursor-zoom-in rounded object-contain"
          />
        ) : kind === 'video' && (preview_url || images?.[0]?.url) ? (
          /* Clickable inline preview — the lightbox owns playback controls
             (G7 review #2: video slots previously rendered nothing). */
          <video
            data-testid="output-video-preview"
            src={mediaSrc(preview_url || images?.[0]?.url)}
            muted
            preload="metadata"
            onDoubleClick={() => openLightbox(0)}
            className="block w-full cursor-zoom-in rounded"
          />
        ) : preview_text ? (
          <div className="line-clamp-4 whitespace-pre-wrap text-sm text-canvas-text">
            {preview_text}
          </div>
        ) : (
          <div className="text-xs italic text-canvas-muted">
            {kind === 'text' ? 'No text yet' : `No ${kind} rendered yet`}
          </div>
        )}
        {gen_recover.length > 0 && (
          <RecoverCell
            // The slot tag travels in the node data — no store lookup, so
            // the overlay works the moment the node renders.
            promptId={
              (data as { gen_slot?: { node_id?: string } }).gen_slot?.node_id ?? null
            }
            taskIds={gen_recover}
            readOnly={readOnly}
          />
        )}
        {gen_failed > 0 && (
          <div
            data-testid="output-failed-chip"
            className="mt-1.5 inline-flex items-center rounded-full border border-rose-400/60 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-rose-400"
          >
            {`${gen_failed} item${gen_failed > 1 ? 's' : ''} failed`}
          </div>
        )}
      </div>
      {commitError && (editorMode !== null || editorOpen) && (
        <div
          data-testid="crop-commit-error"
          role="alert"
          className="absolute left-1/2 top-1/2 z-[51] mt-32 -translate-x-1/2 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {commitError}
        </div>
      )}
      {gridError && editorMode === 'split' && (
        <div
          data-testid="grid-commit-error"
          role="alert"
          className="absolute left-1/2 top-1/2 z-[51] mt-32 -translate-x-1/2 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {gridError}
        </div>
      )}
      {maskError && editorMode === 'mask' && (
        <div
          data-testid="mask-commit-error"
          role="alert"
          className="absolute left-1/2 top-1/2 z-[51] mt-32 -translate-x-1/2 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {maskError}
        </div>
      )}
      {outpaintError && editorMode === 'outpaint' && (
        <div
          data-testid="outpaint-commit-error"
          role="alert"
          className="absolute left-1/2 top-1/2 z-[51] mt-32 -translate-x-1/2 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {outpaintError}
        </div>
      )}
      {preview_url && (
        <UnifiedImageEditor
          open={editorMode !== null}
          src={preview_url}
          alt={preview_text || 'Output preview'}
          initialMode={editorMode ?? 'preview'}
          cropInitialRegion={crop_region ?? undefined}
          outpaintInitialPrompt={preview_text}
          onClose={() => setEditorMode(null)}
          onCropCommit={canCrop ? handleCommit : undefined}
          onOutpaintCommit={canSplit ? handleOutpaintCommit : undefined}
          onMaskCommit={canSplit ? handleMaskCommit : undefined}
          onSplitCommit={canSplit ? handleGridCommit : undefined}
          onBrushCommit={handleBrushCommit}
          onResizeCommit={handleResizeCommit}
          committing={
            committing ||
            gridCommitting ||
            maskCommitting ||
            outpaintCommitting ||
            pixCommitting
          }
        />
      )}
      {lightboxIndex !== null && lightboxItems.length > 0 && (
        <OutputLightbox
          onFrameExported={
            readOnly
              ? undefined
              : (blob, name) => {
                  // IC 导出到画布: the frame lands as a media node beside
                  // this node instead of a silent download.
                  const me = useCanvasCoreStore
                    .getState()
                    .nodes.find((n) => (n as { id?: unknown }).id === id) as
                    | { position?: { x: number; y: number } }
                    | undefined;
                  const at = {
                    x: (me?.position?.x ?? 0) + 460,
                    y: (me?.position?.y ?? 0) + 60,
                  };
                  void createMediaNodeFromFiles(
                    [new File([blob], name, { type: 'image/png' })],
                    at,
                  );
                }
          }
          items={lightboxItems}
          index={Math.min(lightboxIndex, lightboxItems.length - 1)}
          kind={kind === 'video' ? 'video' : 'image'}
          onIndexChange={setLightboxIndex}
          onClose={() => setLightboxIndex(null)}
          editActions={
            readOnly
              ? undefined
              : {
                  ...(canCrop ? { crop: openEditor } : {}),
                  ...(canSplit
                    ? {
                        expand: openOutpaintEditor,
                        mask: openMaskEditor,
                        split: openGridEditor,
                      }
                    : {}),
                }
          }
          meta={(() => {
            // The generating prompt's body (P3-B meta line) — from the
            // source prompt node, snapshotted like compareSources below.
            const promptId = promptIdForOutput(id);
            if (!promptId) return undefined;
            const node = useCanvasCoreStore
              .getState()
              .nodes.find((n) => (n as Record<string, unknown>).id === promptId);
            const body = (
              (node as Record<string, unknown> | undefined)?.data as
                | { body?: string }
                | undefined
            )?.body;
            return body?.trim() || undefined;
          })()}
          compareSources={(() => {
            // Infinite compares result vs the run's INPUT images (thumbnail
            // picker when several qualify); fall back to the newest archived
            // version when there was no image input.
            const promptId = promptIdForOutput(id);
            if (promptId) {
              const store = useCanvasCoreStore.getState();
              const inputs = resolveSourceUrls(promptId, store.nodes, store.connections);
              if (inputs.length > 0) return inputs.map((url) => ({ url }));
            }
            const prev = latestHistoryImageUrl(id);
            return prev ? [{ url: prev }] : [];
          })()}
          onRegenerate={canRegenerate && !readOnly ? onRegenerate : undefined}
          regenerating={regenerating}
        />
      )}
    </div>
  );
}

/** Recover overlay (P1-13 — Infinite's imageTaskRecoverBodyHtml): the poll
 *  broke but the backend task survived — offer a one-shot re-query. */
function RecoverCell({
  promptId,
  taskIds,
  readOnly,
}: {
  promptId: string | null;
  taskIds: string[];
  readOnly?: boolean;
}) {
  const [querying, setQuerying] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const first = taskIds[0];

  const onQuery = useCallback(async () => {
    if (!promptId || !first || querying) return;
    setQuerying(true);
    setNote(null);
    try {
      const outcome = await requeryRecoverTask(promptId, first);
      if (outcome === 'pending') setNote('Still running — check again shortly');
    } finally {
      setQuerying(false);
    }
  }, [promptId, first, querying]);

  if (!first) return null;
  return (
    <div
      data-testid="output-recover-cell"
      className="mt-1.5 rounded-lg border border-amber-400/50 bg-amber-400/10 p-2"
    >
      <div className="text-[11px] font-bold text-amber-500">Task not lost</div>
      <div className="text-[10px] text-canvas-muted">
        {taskIds.length > 1
          ? `${taskIds.length} tasks recoverable · next …${first.slice(-6)}`
          : `Task …${first.slice(-6)}`}
      </div>
      {note && (
        <div data-testid="output-recover-note" className="text-[10px] text-canvas-muted">
          {note}
        </div>
      )}
      <button
        type="button"
        data-testid="output-recover-query"
        onClick={() => void onQuery()}
        disabled={querying || !promptId || readOnly}
        className="nodrag mh-chip mt-1.5 !border-amber-400/60 !text-amber-500 disabled:opacity-50"
      >
        {querying ? 'Checking…' : 'Check Result'}
      </button>
    </div>
  );
}
