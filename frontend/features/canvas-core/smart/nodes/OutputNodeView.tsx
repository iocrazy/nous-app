import { Trash2 } from 'lucide-react';

import { cssAspectRatio, cssAspectRatioOrNull } from '../aspectRatio';
import { mediaSrc } from '../mediaUrl';
import { Handle, Position, type NodeProps } from '@xyflow/react';

import { NodeDeleteButton } from './NodeDeleteButton';
import { NodeWidthGrip } from './NodeWidthGrip';
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

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
  deriveCanvasCrop,
  deriveCanvasGrid,
  deriveCanvasOutpaint,
} from '../../services/canvasService';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { createMediaNode, createOutputNode } from '../factories';
import { genIdFromDurableUrl } from '../mediaEditBridge';
import { upscaleGeneration } from '../../services/canvasGenerationService';
import { createPromptFromNode } from '../recreate';
import { requeryRecoverTask } from '../genResume';
import { latestHistoryImageUrl } from '../outputHistory';
import { resolveSourceUrls } from '../promptInputs';
import { swapEditedImage } from '../swapEditedImage';
import { promptIdForOutput, regenerateForOutput } from '../regenerate';
import { regenKey, useRegenStore } from '../regenStore';
import type { OutputNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { createMediaNodeFromFiles } from '../dropCreate';
import { OutputLightbox, type LightboxItem } from './OutputLightbox';
import { AttachedComposerPanel } from './AttachedComposerPanel';
import { OutputNodeToolbar } from './OutputNodeToolbar';
import { SaveAsAssetDialog } from '../../../../components/assets/SaveAsAssetDialog';
import { useOptionalToast } from '../../../../components/Toast';
import {
  fetchGeneratedItem,
  type GeneratedItem,
} from '../../../../services/generatedService';
import { useCanvasScope } from '../canvasScope';
import { useNodeReveal } from './useNodeReveal';
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

export function OutputNodeView({ id, data, selected }: NodeProps) {
  const {
    kind,
    preview_text,
    preview_url,
    crop_region,
    images,
    history_for,
    gen_pending = 0,
    gen_failed = 0,
    gen_recover = [],
    gen_ratio,
  } = data as unknown as OutputNodeData;
  // The box a cell reserves before anything loads, so a result landing
  // changes no layout (Task 7). `gen_ratio` is plain node data, which keeps
  // this stable under the memo wrapper in nodes/registry.ts.
  //
  // Reserving a box only earns its keep when a placeholder and the thing that
  // replaces it must resolve to ONE element — otherwise it is a guess imposed
  // on media that knows its own shape. So the square fallback is gated on
  // there being something pending to agree with, in BOTH branches.
  //
  // Without that gate the ratio-less nodes get letterboxed permanently:
  // history archives, extend/outpaint results (whose whole point is a CHANGED
  // aspect), upscale tiles, timeline films, loop outputs and entity templates
  // all arrive with no ratio and never regenerate. The history archive is the
  // most visible — it accumulates, so it reaches the multi-image GRID branch
  // after two runs, and it renders the same images as the live slot directly
  // above it. Those size themselves, as they always did.
  const gridAspect =
    gen_pending > 0 ? cssAspectRatio(gen_ratio) : cssAspectRatioOrNull(gen_ratio) ?? undefined;
  const soloAspect = cssAspectRatioOrNull(gen_ratio) ?? undefined;
  // `h-full` only alongside a box. Filling a container that has no height of
  // its own is how the un-boxed branches would collapse instead of falling
  // back to the media's own size, which is the whole point of not boxing
  // them — so the two always travel together.
  const gridFill = gridAspect ? 'h-full ' : '';
  const soloFill = soloAspect ? 'h-full ' : '';
  const patchData = useNodeDataPatch(id);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<EditorMode | null>(null);
  // Grid dblclick: edit THAT image (falls back to the primary preview).
  const [editingUrl, setEditingUrl] = useState<string | null>(null);
  // History nodes carry images[] but NO preview_url (outputHistory archives
  // the image list only) — gating on preview_url alone locked every editing
  // affordance out of them (2026-08-23 "历史卡无法双击进入编辑").
  const primaryImageUrl =
    preview_url || ((images?.[0] as { url?: string } | undefined)?.url ?? null);
  const canCrop = kind === 'image' && !!primaryImageUrl;
  // The image an edit acts on: the grid item that was double-clicked, else the
  // primary. Every derive keys on THIS url — never on the node's legacy
  // resource_id, which only ever named the primary, and only after a promote.
  const editSourceUrl = editingUrl ?? primaryImageUrl;
  // Leaving the editor forgets which item was being edited; otherwise the
  // toolbar's Crop would reopen on a grid item double-clicked long ago.
  useEffect(() => {
    if (editorMode === null) setEditingUrl(null);
  }, [editorMode]);
  const [upscaling, setUpscaling] = useState(false);
  // IC duplicateSmartNodeMediaToCanvas: drop the current image beside this
  // node as an independent media card (no re-upload — same durable url).
  const handleDuplicate = useCallback(() => {
    const url = preview_url || (images?.[0] as { url?: string } | undefined)?.url;
    if (!url) return;
    const store = useCanvasCoreStore.getState();
    const self = store.nodes.find((n) => (n as { id?: string }).id === id) as
      | { position?: { x: number; y: number } }
      | undefined;
    const base = self?.position ?? { x: 0, y: 0 };
    const node = createMediaNode(
      { title: 'Copy', items: [{ url, kind: 'image' }] },
      {
        position: {
          x: base.x + SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X,
          y: base.y + 40,
        },
      },
    );
    store.setNodes([...store.nodes, node]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview_url, images, id]);
  // IC 放大: upscale the primary image via jimeng image_upscale; the result
  // appends beside the original (source stays).
  const handleUpscale = useCallback(() => {
    const url = primaryImageUrl;
    const genId = url ? genIdFromDurableUrl(url) : null;
    if (!genId) return;
    void (async () => {
      try {
        setUpscaling(true);
        const out = await upscaleGeneration(genId, '2k');
        const current = (useCanvasCoreStore
          .getState()
          .nodes.find((n) => (n as { id?: string }).id === id) as
          | { data?: { images?: Array<{ url: string }> } }
          | undefined)?.data;
        patchData({
          images: [
            ...((current?.images as Array<{ url: string }>) ?? []),
            { url: out.url, kind: 'image', name: 'upscaled.png' },
          ],
        });
      } catch (err) {
        console.error('upscale failed:', err);
      } finally {
        setUpscaling(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview_url, images, id, patchData]);
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
    (composite: Blob) => {
      if (!editSourceUrl) return;
      void (async () => {
        try {
          setPixCommitting(true);
          const file = new File([composite], 'brush.png', { type: 'image/png' });
          // Classified at the point of upload: this composite is an INPUT the
          // editor baked, not something the user asked the library for. Left
          // unclassified it lands in the Generated inbox looking exactly like
          // a file they chose.
          const item = await importCanvasMedia(file, canvasId, id, 'brush');
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
    [editSourceUrl, canvasId, id, images, patchData],
  );
  const handleResizeCommit = useCallback(
    (scale: number) => {
      if (!editSourceUrl) return;
      void (async () => {
        try {
          setPixCommitting(true);
          const blob = await bakeResize(editSourceUrl, scale);
          const file = new File([blob], 'resized.png', { type: 'image/png' });
          // A resize is a product the user asked for — visible in the inbox,
          // same role as the server-side derives.
          const item = await importCanvasMedia(file, canvasId, id, 'derived');
          patchData({
            images: [
              ...((images as Array<{ url: string }>) ?? []),
              { url: item.url, kind: 'image', name: 'resized.png', id: item.id },
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
    [editSourceUrl, canvasId, id, images, patchData],
  );

  const canRegenerate = !!promptIdForOutput(id);
  const onRegenerate = useCallback(() => {
    void regenerateForOutput(id);
  }, [id]);

  // ── "As Asset" (P4 Task 6) ────────────────────────────────────────────
  //
  // Promoting an output into the asset library needs the `generated_media`
  // ROW, not the picture: `SaveAsAssetDialog` takes `GeneratedItem`s, and its
  // `source_asset_id` prefill — which is what makes "regenerate this
  // character's sheet" land back on that character — is a column the node has
  // never held. So the id is resolved here and the row is fetched on demand.
  //
  // The id has TWO sources, in this order:
  //   1. `GeneratedImageRef.id`, which both import endpoints have always sent;
  //   2. failing that, the durable url itself — `/api/v1/generated-media/{id}/…`
  //      carries the same id, and `handleUpscale` above already recovers it
  //      that way, so a node persisted before the field existed is not shut
  //      out of the library for a reason it cannot see.
  //
  // Neither available (a pasted external url, a bare `preview_text`) leaves
  // the key DISABLED WITH A REASON rather than hidden. Same for a canvas
  // opened outside a `/team/:teamId` route: `/api/v1/assets` is scoped per
  // request and an empty `scope_id` is a 403, not an unscoped query.
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const { scopeId } = useCanvasScope();
  const generationId =
    (images?.[0] as { id?: string } | undefined)?.id ??
    (primaryImageUrl ? genIdFromDurableUrl(primaryImageUrl) : null);
  const [asAssetItem, setAsAssetItem] = useState<GeneratedItem | null>(null);
  const [asAssetLoading, setAsAssetLoading] = useState(false);
  const asAssetDisabledReason = !scopeId
    ? t('canvas.asAsset.noScope', 'Open this canvas from a workspace to save assets')
    : !generationId
      ? t('canvas.asAsset.noGeneration', 'This image has no library record to save')
      : undefined;

  const handleAsAsset = useCallback(() => {
    if (!scopeId || !generationId || asAssetLoading) return;
    setAsAssetLoading(true);
    fetchGeneratedItem(scopeId, generationId)
      .then((item) => setAsAssetItem(item))
      .catch((err) => {
        // Never silent: the user clicked, and "nothing happened" is the one
        // outcome that teaches them the button is broken.
        console.error('[OutputNodeView] fetchGeneratedItem failed:', err);
        toast?.addToast(
          t('canvas.asAsset.lookupFailed', 'Could not read this generation'),
          'error',
        );
      })
      .finally(() => setAsAssetLoading(false));
  }, [scopeId, generationId, asAssetLoading, toast, t]);

  // Crop / Expand / Split derive server-side and file the product under this
  // canvas — any displayable image qualifies, wherever it came from.
  const canDerive = canCrop && !!canvasId;

  const openEditor = useCallback(() => {
    if (!canDerive) return;
    setCommitError(null);
    setEditorMode('crop');
  }, [canDerive]);

  const closeEditor = useCallback(() => {
    setEditorOpen(false);
        setEditorMode(null);
    setCommitError(null);
  }, []);

  const handleCommit = useCallback(
    async (region: CropRegion) => {
      if (!canvasId || !editSourceUrl) return;
      try {
        setCommitting(true);
        setCommitError(null);
        const derived = await deriveCanvasCrop(canvasId, editSourceUrl, region, {
          nodeId: id,
        });
        // Replace THE edited image (primary, one grid item, or a history
        // item) with its cropped copy — a durable generated-media url, no
        // session token.
        patchData({ ...swapEditedImage({ preview_url, images }, editSourceUrl, derived) });
        setEditorOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to derive crop';
        setCommitError(message);
        // Leave the modal open so the user can retry or cancel.
      } finally {
        setCommitting(false);
      }
    },
    [canvasId, editSourceUrl, id, preview_url, images, patchData],
  );

  const openOutpaintEditor = useCallback(() => {
    if (!canDerive) return;
    setOutpaintError(null);
    setEditorMode('outpaint');
  }, [canDerive]);

  const closeOutpaintEditor = useCallback(() => {
    setOutpaintOpen(false);
        setEditorMode(null);
    setOutpaintError(null);
  }, []);

  const handleOutpaintCommit = useCallback(
    async (padding: OutpaintPadding, prompt: string) => {
      if (!canvasId || !editSourceUrl) return;
      try {
        setOutpaintCommitting(true);
        setOutpaintError(null);
        const derived = await deriveCanvasOutpaint(canvasId, editSourceUrl, padding, {
          nodeId: id,
          prompt: prompt || undefined,
        });
        // Spawn the extended image as a fresh node beside the source.
        const store = useCanvasCoreStore.getState();
        const self = store.nodes.find(
          (node) => (node as { id?: string }).id === id,
        ) as { position?: { x: number; y: number } } | undefined;
        const base = self?.position ?? { x: 0, y: 0 };
        const extendedNode = createOutputNode(
          { kind: 'image', preview_text: '', preview_url: derived.url },
          {
            position: {
              x: base.x + SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X,
              y: base.y,
            },
          },
        );
        store.setNodes([...store.nodes, extendedNode]);
        // IC 扩图联动: pre-seed a wired prompt with IC's own instruction.
        const extId = String((extendedNode as unknown as { id?: unknown }).id ?? '');
        if (extId) {
          createPromptFromNode(extId, {
            body: 'Remove the white area and fill the scene naturally',
            gen: { kind: 'image', model: '', ratio: 'auto', count: 1 },
            source_ref: derived.url,
          });
        }
        setOutpaintOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to extend canvas';
        setOutpaintError(message);
      } finally {
        setOutpaintCommitting(false);
      }
    },
    [canvasId, editSourceUrl, id],
  );

  const openMaskEditor = useCallback(() => {
    if (!canCrop) return;
    setMaskError(null);
    setEditorMode('mask');
  }, [canCrop]);

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
      try {
        setMaskCommitting(true);
        setMaskError(null);
        // IC 生成遮罩: the black/white mask lands INSIDE this node, side by
        // side with the original (one node feeds 图1+图2 downstream) — not
        // as a separate card (2026-08-21 "遮罩直接在外面显示").
        const maskB64 = strokesToMaskPngBase64(strokes, size.width, size.height);
        const bin = atob(maskB64.split(',').pop() ?? maskB64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'mask.png', { type: 'image/png' });
        // Same reason as the brush bake: a black-and-white mask is machine
        // output feeding the next generation, never inbox triage material.
        const item = await importCanvasMedia(file, canvasId, id, 'mask');
        const current = (useCanvasCoreStore
          .getState()
          .nodes.find((n) => (n as { id?: string }).id === id) as
          | { data?: { images?: Array<{ url: string }> } }
          | undefined)?.data;
        patchData({
          images: [
            ...((current?.images as Array<{ url: string }>) ?? []),
            { url: item.url, kind: 'image', name: 'mask.png' },
          ],
        });
        setMaskOpen(false);
        setEditorMode(null);
      } catch (err) {
        console.error('mask append failed:', err);
        setMaskError(err instanceof Error ? err.message : 'Mask failed');
      } finally {
        setMaskCommitting(false);
      }
    },
    [id, canvasId, patchData],
  );


  const openGridEditor = useCallback(() => {
    if (!canDerive) return;
    setGridError(null);
    setEditorMode('split');
  }, [canDerive]);

  const closeGridEditor = useCallback(() => {
    setGridOpen(false);
        setEditorMode(null);
    setGridError(null);
  }, []);

  const handleGridCommit = useCallback(
    async (lines: GridLines) => {
      if (!canvasId || !editSourceUrl) return;
      try {
        setGridCommitting(true);
        setGridError(null);
        const tiles = await deriveCanvasGrid(canvasId, editSourceUrl, lines, {
          nodeId: id,
        });
        // One output node per tile, mirroring the grid to the right of the
        // source node. The source node itself is left untouched.
        const store = useCanvasCoreStore.getState();
        const self = store.nodes.find(
          (node) => (node as { id?: string }).id === id,
        ) as { position?: { x: number; y: number } } | undefined;
        const base = self?.position ?? { x: 0, y: 0 };
        const startX = base.x + SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X;
        const tileNodes = tiles.map((tile) =>
          createOutputNode(
            { kind: 'image', preview_text: '', preview_url: tile.url },
            {
              position: {
                x:
                  startX +
                  (tile.col ?? 0) * (SMART_NODE_DEFAULT_WIDTH.output + TILE_LAYOUT_GAP_X),
                y: base.y + (tile.row ?? 0) * TILE_LAYOUT_STEP_Y,
              },
            },
          ),
        );
        store.setNodes([...store.nodes, ...tileNodes]);
        setGridOpen(false);
        setEditorMode(null);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to split image';
        setGridError(message);
      } finally {
        setGridCommitting(false);
      }
    },
    [canvasId, editSourceUrl, id],
  );

  // Mount the floating toolbar only while the card is hovered / focused
  // (fluency T5) — see useNodeReveal.
  const { revealed, revealHandlers } = useNodeReveal();

  return (
    <div
      data-testid="smart-output-node"
      className={`group mh-node relative border-canvas-line ${selected ? 'mh-node-selected' : ''}`}
      {...revealHandlers}
      style={{
        // Width lives in node DATA (persisted), never in RF's measured
        // width — reading props.width created a measurement feedback loop
        // (2026-08-20: cards locked tiny/huge at whatever RF measured).
        width: (data as { node_w?: number }).node_w ?? SMART_NODE_DEFAULT_WIDTH.output,
      }}
    >
      {/* IC node-resize-handle: drag the right edge to widen the card
          (media grids reflow; height stays content-driven). RF applies the
          resized width to the node wrapper — the root div tracks it via
          width:100% with the legacy default as its floor. */}
      {!readOnly && (
        <NodeWidthGrip
          value={(data as { node_w?: number }).node_w ?? SMART_NODE_DEFAULT_WIDTH.output}
          min={SMART_NODE_DEFAULT_WIDTH.output}
          max={900}
          onChange={(w) => patchData({ node_w: w } as never)}
        />
      )}
      <Handle
        type="target"
        position={Position.Left}
      />
      {/* IC parity: results are image SOURCES — the right port wires the
          generated image into downstream prompts / loops / groups. */}
      <Handle type="source" position={Position.Right} />
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
          hovered={revealed}
          onPreview={() => openLightbox(0)}
          onCrop={canDerive ? openEditor : undefined}
          onExpand={canDerive ? openOutpaintEditor : undefined}
          onMask={canCrop ? openMaskEditor : undefined}
          onBrush={() => setEditorMode('brush')}
          onUpscale={canCrop ? handleUpscale : undefined}
          onDuplicate={canCrop ? handleDuplicate : undefined}
          upscaling={upscaling}
          onSplit={canDerive ? openGridEditor : undefined}
          onRerun={canRegenerate ? onRegenerate : undefined}
          rerunning={regenerating}
          onAsAsset={handleAsAsset}
          asAssetDisabled={asAssetDisabledReason !== undefined || asAssetLoading}
          asAssetDisabledReason={asAssetDisabledReason}
          readOnly={readOnly}
        />
      )}
      <div className="mh-node-head">
        <div className="mh-node-title">
          {history_for ? 'History · ' : ''}
          Output · {KIND_LABEL[kind]}
        </div>
        <div className="flex items-center gap-2">
          {/* Editing actions live ONLY on the floating toolbar (IC single
              toolbar — the old header chip row duplicated it, 2026-08-21
              "怎么有两排"). Status badges stay. */}
          {crop_region && (
            <div
              data-testid="crop-region-badge"
              className="text-[10px] uppercase tracking-wider text-canvas-strong"
              title="Crop applied"
            >
              Cropped
            </div>
          )}
        </div>
      </div>
      <div
        data-testid="smart-output-body"
        // Double-click: images jump straight into the rich editor (IC
        // dblclick → imageEditModal); video/text keep the lightbox. Single
        // click falls through to React Flow node selection.
        onDoubleClick={() => {
          const firstUrl = primaryImageUrl;
          if (kind === 'image' && firstUrl && !readOnly) {
            setEditingUrl(firstUrl);
            setEditorMode('preview');
          } else {
            openLightbox(0);
          }
        }}
        className={`p-3 ${lightboxItems.length > 0 ? 'cursor-zoom-in' : ''}`}
        title={lightboxItems.length > 0 ? 'Double-click to preview' : undefined}
      >
        {gen_pending > 0 || (images?.length ?? 0) + gen_pending > 1 ? (
          /* Multi-result grid (G4-F2) + in-flight shimmer cells (P0-3):
             the slot shows WHERE results land the moment the run is
             dispatched; each finished item replaces a cell as it arrives. */
          <div className="grid grid-cols-2 gap-1" data-testid="output-images-grid">
            {(images ?? []).map((img, i) => (
              <div
                key={`${img.url}-${i}`}
                data-testid="output-cell"
                className="group/cell relative"
                style={{ aspectRatio: gridAspect }}
              >
                <img
                  src={mediaSrc(img.url)}
                  alt={img.name || `Generated ${i + 1}`}
                  draggable={false}
                  // Off-screen nodes on a big canvas must not each cost a
                  // fetch + a main-thread decode the moment they mount.
                  loading="lazy"
                  decoding="async"
                  onDoubleClick={(e) => {
                    // IC: dblclick edits THIS image (a mask can be repainted);
                    // single click keeps the lightbox via the body handler.
                    e.stopPropagation();
                    if (readOnly) {
                      openLightbox(i);
                      return;
                    }
                    setEditingUrl(img.url);
                    setEditorMode('preview');
                  }}
                  className={`block ${gridFill}w-full cursor-zoom-in rounded object-contain`}
                />
                {/* IC 图四: each grid item (e.g. a generated mask) can be
                    removed on its own. */}
                {!readOnly && (
                  <button
                    type="button"
                    data-testid={`output-image-delete-${i}`}
                    aria-label="Remove image"
                    onClick={(e) => {
                      e.stopPropagation();
                      patchData({
                        images: (images ?? []).filter((_, j) => j !== i),
                      });
                    }}
                    className="absolute right-1 top-1 z-[3] flex h-6 w-6 items-center justify-center rounded-full bg-rose-500/90 text-white opacity-0 transition-opacity group-hover/cell:opacity-100"
                  >
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            ))}
            {Array.from({ length: gen_pending }, (_, i) => (
              // The shimmer sits INSIDE the reserved box rather than being
              // the box, so the wrapper an image later occupies and the
              // wrapper a placeholder occupies are the same element type
              // with the same style — nothing to reflow at the swap.
              <div
                key={`pending-${i}`}
                data-testid="output-cell"
                className="w-full"
                style={{ aspectRatio: gridAspect }}
              >
                <div
                  data-testid="output-pending-cell"
                  aria-label="Generating"
                  className="mh-loading-cell h-full w-full rounded"
                />
              </div>
            ))}
          </div>
        ) : kind === 'image' && (preview_url || images?.[0]?.url) ? (
          <div
            data-testid="output-cell"
            className="w-full"
            style={{ aspectRatio: soloAspect }}
          >
            <img
              src={mediaSrc(preview_url || images?.[0]?.url)}
              alt={preview_text || 'Output preview'}
              draggable={false}
              loading="lazy"
              decoding="async"
              onDoubleClick={(e) => {
                e.stopPropagation();
                if (!readOnly) setEditorMode('preview');
                else openLightbox(0);
              }}
              className={`block ${soloFill}w-full cursor-zoom-in rounded object-contain`}
            />
          </div>
        ) : kind === 'video' && (preview_url || images?.[0]?.url) ? (
          /* Clickable inline preview — the lightbox owns playback controls
             (G7 review #2: video slots previously rendered nothing). */
          <div
            data-testid="output-cell"
            className="w-full"
            style={{ aspectRatio: soloAspect }}
          >
            <video
              data-testid="output-video-preview"
              src={mediaSrc(preview_url || images?.[0]?.url)}
              muted
              preload="metadata"
              onDoubleClick={() => openLightbox(0)}
              className={`block ${soloFill}w-full cursor-zoom-in rounded object-contain`}
            />
          </div>
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
      {editSourceUrl && (
        <UnifiedImageEditor
          open={editorMode !== null}
          src={editSourceUrl ?? ''}
          alt={preview_text || 'Output preview'}
          initialMode={editorMode ?? 'preview'}
          cropInitialRegion={crop_region ?? undefined}
          outpaintInitialPrompt={preview_text}
          onClose={() => {
            setEditorMode(null);
            setEditingUrl(null);
          }}
          onCropCommit={canDerive ? handleCommit : undefined}
          onOutpaintCommit={canDerive ? handleOutpaintCommit : undefined}
          onMaskCommit={canCrop ? handleMaskCommit : undefined}
          onSplitCommit={canDerive ? handleGridCommit : undefined}
          onBrushCommit={handleBrushCommit}
          onResizeCommit={handleResizeCommit}
          // The node-level banners above render under the editor's body
          // portal; the dialog shows the same reason where the user can see it.
          commitError={commitError ?? gridError ?? maskError ?? outpaintError}
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
                  ...(canDerive
                    ? { crop: openEditor, expand: openOutpaintEditor, split: openGridEditor }
                    : {}),
                  ...(canCrop ? { mask: openMaskEditor } : {}),
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
      {/* PORTALLED TO THE BODY ON PURPOSE. `UiModal` positions itself with
          `position: fixed` and does not portal — and every ancestor of a node
          here is CSS-transformed by React Flow, which makes `fixed` resolve
          against the node's own box instead of the viewport. Rendered in
          place, the dialog would open as a squashed panel inside a 260px
          card. Same trap `OutputNodeToolbar`'s header names. */}
      {asAssetItem &&
        scopeId &&
        createPortal(
          <SaveAsAssetDialog
            open
            scopeId={scopeId}
            items={[asAssetItem]}
            onClose={() => setAsAssetItem(null)}
            onDone={(outcome) => {
              setAsAssetItem(null);
              // The node itself does NOT change. The picture is still the
              // canvas's output; what changed is that a library asset now
              // also points at it. Stamping the card would claim the two are
              // the same object, and a second promotion into another asset
              // would then have to overwrite the first.
              toast?.addToast(
                t('canvas.asAsset.saved', 'Saved to {{name}}', {
                  name: outcome.assetName,
                }),
                'success',
              );
            }}
          />,
          document.body,
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
