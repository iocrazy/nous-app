import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { getResourceFileUrl } from '../../../../services/resourceService';
import { getSupabaseClient } from '../../../../supabaseClient';
import { CropEditorModal } from '../../editor/CropEditorModal';
import { GridSplitEditorModal } from '../../editor/GridSplitEditorModal';
import { type GridLines } from '../../editor/gridMath';
import { MaskEditorModal } from '../../editor/MaskEditorModal';
import { strokesToMaskPngBase64 } from '../../editor/maskExport';
import { type MaskStroke } from '../../editor/maskMath';
import { OutpaintEditorModal } from '../../editor/OutpaintEditorModal';
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
import { latestHistoryImageUrl } from '../outputHistory';
import { resolveSourceUrls } from '../promptInputs';
import { promptIdForOutput, regenerateForOutput } from '../regenerate';
import { regenKey, useRegenStore } from '../regenStore';
import type { OutputNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { OutputLightbox, type LightboxItem } from './OutputLightbox';
import { useNodeDataPatch } from './useNodeDataPatch';

/** Single-click waits this long for a possible double-click (crop) before
 *  opening the lightbox — the two gestures share the same image. */
const LIGHTBOX_CLICK_DELAY_MS = 250;

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
  } = data as unknown as OutputNodeData;
  const patchData = useNodeDataPatch(id);
  const [editorOpen, setEditorOpen] = useState(false);
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
  const lightboxTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const regenerating = useRegenStore((s) => !!s.running[regenKey(canvasId, id)]);

  useEffect(
    () => () => {
      if (lightboxTimerRef.current) clearTimeout(lightboxTimerRef.current);
    },
    [],
  );

  const lightboxItems: LightboxItem[] =
    images && images.length > 0
      ? images.map((img) => ({ url: img.url, name: img.name }))
      : kind !== 'text' && preview_url
        ? [{ url: preview_url, name: preview_text || undefined }]
        : [];

  /** Deferred so a double-click (crop) can cancel the pending open. */
  const queueLightbox = useCallback(
    (index: number) => {
      if (lightboxItems.length === 0) return;
      if (lightboxTimerRef.current) clearTimeout(lightboxTimerRef.current);
      lightboxTimerRef.current = setTimeout(() => {
        lightboxTimerRef.current = null;
        setLightboxIndex(index);
      }, LIGHTBOX_CLICK_DELAY_MS);
    },
    [lightboxItems.length],
  );

  const cancelQueuedLightbox = useCallback(() => {
    if (lightboxTimerRef.current) {
      clearTimeout(lightboxTimerRef.current);
      lightboxTimerRef.current = null;
    }
  }, []);

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
    setEditorOpen(true);
  }, [canCrop]);

  const closeEditor = useCallback(() => {
    setEditorOpen(false);
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
    setOutpaintOpen(true);
  }, [canSplit]);

  const closeOutpaintEditor = useCallback(() => {
    setOutpaintOpen(false);
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
    setMaskOpen(true);
  }, [canSplit]);

  const closeMaskEditor = useCallback(() => {
    setMaskOpen(false);
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
    setGridOpen(true);
  }, [canSplit]);

  const closeGridEditor = useCallback(() => {
    setGridOpen(false);
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
      className={`mh-node border-canvas-line ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.output }}
    >
      <Handle
        type="target"
        position={Position.Left}
      />
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
              disabled={regenerating}
              title="Re-run the source prompt"
              className="mh-chip"
            >
              {regenerating ? 'Rerunning…' : 'Rerun'}
            </button>
          )}
          {canSplit && (
            <button
              type="button"
              data-testid="outpaint-open"
              onClick={openOutpaintEditor}
              title="Extend the canvas beyond the image"
              className="mh-chip"
            >
              Expand
            </button>
          )}
          {canSplit && (
            <button
              type="button"
              data-testid="mask-cutout-open"
              onClick={openMaskEditor}
              title="Paint a region to cut out"
              className="mh-chip"
            >
              Mask
            </button>
          )}
          {canSplit && (
            <button
              type="button"
              data-testid="grid-split-open"
              onClick={openGridEditor}
              title="Split into a grid of tiles"
              className="mh-chip"
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
        onDoubleClick={() => {
          cancelQueuedLightbox();
          openEditor();
        }}
        className={`p-3 ${canCrop ? 'cursor-zoom-in' : ''}`}
        title={canCrop ? 'Double-click to crop' : undefined}
      >
        {gen_pending > 0 || (images?.length ?? 0) + gen_pending > 1 ? (
          /* Multi-result grid (G4-F2) + in-flight shimmer cells (P0-3):
             the slot shows WHERE results land the moment the run is
             dispatched; each finished item replaces a cell as it arrives. */
          <div className="grid grid-cols-2 gap-1" data-testid="output-images-grid">
            {(images ?? []).map((img, i) => (
              <img
                key={`${img.url}-${i}`}
                src={img.url}
                alt={img.name || `Generated ${i + 1}`}
                draggable={false}
                onClick={() => queueLightbox(i)}
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
            src={preview_url || images?.[0]?.url}
            alt={preview_text || 'Output preview'}
            draggable={false}
            onClick={() => queueLightbox(0)}
            className="block w-full rounded object-contain"
          />
        ) : kind === 'video' && (preview_url || images?.[0]?.url) ? (
          /* Clickable inline preview — the lightbox owns playback controls
             (G7 review #2: video slots previously rendered nothing). */
          <video
            data-testid="output-video-preview"
            src={preview_url || images?.[0]?.url}
            muted
            preload="metadata"
            onClick={() => queueLightbox(0)}
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
        {gen_failed > 0 && (
          <div
            data-testid="output-failed-chip"
            className="mt-1.5 inline-flex items-center rounded-full border border-rose-400/60 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-rose-400"
          >
            {`${gen_failed} item${gen_failed > 1 ? 's' : ''} failed`}
          </div>
        )}
      </div>
      {canCrop && preview_url && (
        <CropEditorModal
          open={editorOpen}
          src={preview_url}
          alt={preview_text || 'Output preview'}
          initialRegion={crop_region ?? FULL_REGION}
          onCommit={handleCommit}
          onCancel={closeEditor}
          committing={committing}
        />
      )}
      {commitError && editorOpen && (
        <div
          data-testid="crop-commit-error"
          role="alert"
          className="absolute left-1/2 top-1/2 z-[51] mt-32 -translate-x-1/2 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {commitError}
        </div>
      )}
      {canSplit && preview_url && (
        <GridSplitEditorModal
          open={gridOpen}
          src={preview_url}
          alt={preview_text || 'Output preview'}
          onCommit={handleGridCommit}
          onCancel={closeGridEditor}
          committing={gridCommitting}
        />
      )}
      {gridError && gridOpen && (
        <div
          data-testid="grid-commit-error"
          role="alert"
          className="absolute left-1/2 top-1/2 z-[51] mt-32 -translate-x-1/2 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {gridError}
        </div>
      )}
      {canSplit && preview_url && (
        <MaskEditorModal
          open={maskOpen}
          src={preview_url}
          alt={preview_text || 'Output preview'}
          onCommit={handleMaskCommit}
          onCancel={closeMaskEditor}
          committing={maskCommitting}
        />
      )}
      {maskError && maskOpen && (
        <div
          data-testid="mask-commit-error"
          role="alert"
          className="absolute left-1/2 top-1/2 z-[51] mt-32 -translate-x-1/2 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {maskError}
        </div>
      )}
      {canSplit && preview_url && (
        <OutpaintEditorModal
          open={outpaintOpen}
          src={preview_url}
          alt={preview_text || 'Output preview'}
          initialPrompt={preview_text}
          onCommit={handleOutpaintCommit}
          onCancel={closeOutpaintEditor}
          committing={outpaintCommitting}
        />
      )}
      {outpaintError && outpaintOpen && (
        <div
          data-testid="outpaint-commit-error"
          role="alert"
          className="absolute left-1/2 top-1/2 z-[51] mt-32 -translate-x-1/2 rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {outpaintError}
        </div>
      )}
      {lightboxIndex !== null && lightboxItems.length > 0 && (
        <OutputLightbox
          items={lightboxItems}
          index={Math.min(lightboxIndex, lightboxItems.length - 1)}
          kind={kind === 'video' ? 'video' : 'image'}
          onIndexChange={setLightboxIndex}
          onClose={() => setLightboxIndex(null)}
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
          onRegenerate={canRegenerate ? onRegenerate : undefined}
          regenerating={regenerating}
        />
      )}
    </div>
  );
}
