import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useCallback, useState } from 'react';

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
import type { OutputNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
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
  const { kind, resource_id, preview_text, preview_url, crop_region } =
    data as unknown as OutputNodeData;
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
      className={`rounded-md border-2 bg-white shadow dark:bg-slate-900 ${
        selected
          ? 'border-indigo-500'
          : 'border-slate-300 dark:border-slate-700'
      }`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.output }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !bg-slate-400"
      />
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1.5 dark:border-slate-700">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Output · {KIND_LABEL[kind]}
        </div>
        <div className="flex items-center gap-2">
          {canSplit && (
            <button
              type="button"
              data-testid="outpaint-open"
              onClick={openOutpaintEditor}
              title="Extend the canvas beyond the image"
              className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
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
              className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
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
              className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Split
            </button>
          )}
          {crop_region && (
            <div
              data-testid="crop-region-badge"
              className="text-[10px] uppercase tracking-wider text-indigo-600"
              title="Crop applied"
            >
              Cropped
            </div>
          )}
          {resource_id && (
            <div className="text-[10px] uppercase tracking-wider text-emerald-600">
              Saved
            </div>
          )}
        </div>
      </div>
      <div
        data-testid="smart-output-body"
        onDoubleClick={openEditor}
        className={`p-3 ${canCrop ? 'cursor-zoom-in' : ''}`}
        title={canCrop ? 'Double-click to crop' : undefined}
      >
        {kind === 'image' && preview_url ? (
          <img
            src={preview_url}
            alt={preview_text || 'Output preview'}
            draggable={false}
            className="block w-full rounded object-contain"
          />
        ) : preview_text ? (
          <div className="line-clamp-4 whitespace-pre-wrap text-sm text-slate-800 dark:text-slate-200">
            {preview_text}
          </div>
        ) : (
          <div className="text-xs italic text-slate-400">
            {kind === 'text' ? 'No text yet' : `No ${kind} rendered yet`}
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
    </div>
  );
}
