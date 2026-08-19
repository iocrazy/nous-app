/**
 * MediaNodeView — the smart canvas's Upload card (Infinite's 上传节点).
 *
 * Empty state invites a click / file-drop; uploads stream through
 * POST /generated-media/import so every item is a durable
 * /generated-media/ URL a connected prompt can use as an i2i source.
 * Items render as a 2-col thumbnail grid (images <img>, videos <video>).
 */

import { mediaSrc } from '../mediaUrl';
import { useCallback, useRef, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Loader2, UploadCloud } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import {
  CANVAS_MEDIA_ACCEPT,
  CANVAS_MEDIA_MAX_BYTES,
  importCanvasMedia,
  isImportableCanvasFile,
} from '../mediaImport';
import type { GeneratedImageRef, MediaNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { AttachedComposerPanel } from './AttachedComposerPanel';
import { OutputNodeToolbar } from './OutputNodeToolbar';
import { MediaItemEditor } from './MediaItemEditor';
import type { EditorMode } from '../../editor/UnifiedImageEditor';
import { OutputLightbox, type LightboxItem } from './OutputLightbox';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';

/** Which lightbox is open — a kind + the index within that kind's own
 *  item list (OutputLightbox navigates a single kind at a time, so a
 *  mixed image/video grid needs to route to the matching sub-list). */
interface LightboxState {
  kind: 'image' | 'video';
  index: number;
}

export function MediaNodeView({ id, data, selected }: NodeProps) {
  const { t } = useTranslation();
  const { title, items, uploading } = data as unknown as MediaNodeData;
  const patch = useNodeDataPatch(id);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<LightboxState | null>(null);
  // W×H per cell, filled from the <img> natural size on load (IC's
  // image-resolution-badge).
  const [resBadges, setResBadges] = useState<Record<number, string>>({});
  const [editState, setEditState] = useState<{
    item: GeneratedImageRef;
    mode: EditorMode;
  } | null>(null);
  const removeItem = (idx: number) => {
    const current = (useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as { id?: unknown }).id === id) as
      | { data?: MediaNodeData }
      | undefined)?.data;
    patch({ items: (current?.items ?? []).filter((_, i) => i !== idx) });
  };
  // Read-only: every upload path goes (Add, the empty-state click, the
  // file drop) — they POST an import AND patch the node. Thumbnails and
  // the lightbox are pure viewing and stay.
  const readOnly = useCanvasReadOnly();

  const uploadFiles = useCallback(
    async (files: File[]) => {
      const importable = files.filter(
        (f) => isImportableCanvasFile(f) && f.size <= CANVAS_MEDIA_MAX_BYTES,
      );
      const rejected = files.length - importable.length;
      setError(
        rejected > 0
          ? t('canvas.mediaNode.rejected', 'Only images/videos up to 50MB')
          : null,
      );
      if (importable.length === 0) return;

      // Reserve shimmer cells up-front; settle them one by one so the
      // first finished upload shows immediately (no all-or-nothing wait).
      const store = useCanvasCoreStore.getState;
      const dataOf = () =>
        ((store()
          .nodes.find((n) => (n as { id?: unknown }).id === id) as
          | { data?: MediaNodeData }
          | undefined)?.data ?? { title: '', items: [] }) as MediaNodeData;
      patch({ uploading: (dataOf().uploading ?? 0) + importable.length });

      for (const file of importable) {
        try {
          const item: GeneratedImageRef = await importCanvasMedia(
            file,
            canvasId,
            id,
          );
          const current = dataOf();
          patch({
            items: [...(current.items ?? []), item],
            uploading: Math.max(0, (current.uploading ?? 1) - 1),
          });
        } catch (err) {
          console.error('[MediaNodeView] upload failed:', err);
          const current = dataOf();
          patch({ uploading: Math.max(0, (current.uploading ?? 1) - 1) });
          setError(t('canvas.mediaNode.uploadFailed', 'Upload failed'));
        }
      }
    },
    [canvasId, id, patch, t],
  );

  const pendingCells = Math.max(0, uploading ?? 0);
  const isEmpty = (items?.length ?? 0) === 0 && pendingCells === 0;

  // Split by kind so OutputLightbox (single-kind navigation) gets a
  // clean list to page through — clicking an image never lands the
  // lightbox on a video slot and vice versa.
  const imageItems: LightboxItem[] = (items ?? [])
    .filter((item) => item.kind !== 'video')
    .map((item) => ({ url: item.url, name: item.name }));
  const videoItems: LightboxItem[] = (items ?? [])
    .filter((item) => item.kind === 'video')
    .map((item) => ({ url: item.url, name: item.name }));
  const lightboxItems = lightbox?.kind === 'video' ? videoItems : imageItems;
  // Image refs in imageItems order — maps a lightbox index back to the item.
  const imageRefs = (items ?? []).filter((it) => it.kind !== 'video');
  const openItemEditor = (imageIndex: number, mode: EditorMode) => {
    const target = imageRefs[imageIndex];
    if (!target) return;
    setLightbox(null);
    setEditState({ item: target, mode });
  };

  return (
    <div
      data-testid="smart-media-node"
      className={`group relative mh-node border-canvas-line ${selected ? 'mh-node-selected' : ''} ${
        dragOver ? 'ring-2 ring-indigo-500/50' : ''
      }`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.media }}
      onDragOver={(e) => {
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
      {(items?.length ?? 0) > 0 && (
        <OutputNodeToolbar
          items={(items ?? []).map((it) => ({ url: it.url, name: it.name }))}
          pinned={Boolean(selected)}
          onPreview={() =>
            setLightbox({
              kind: (items ?? [])[0]?.kind === 'video' ? 'video' : 'image',
              index: 0,
            })
          }
          onCrop={
            !readOnly && imageItems.length > 0
              ? () => openItemEditor(0, 'crop')
              : undefined
          }
          onExpand={
            !readOnly && imageItems.length > 0
              ? () => openItemEditor(0, 'outpaint')
              : undefined
          }
          onMask={
            !readOnly && imageItems.length > 0
              ? () => openItemEditor(0, 'mask')
              : undefined
          }
          onSplit={
            !readOnly && imageItems.length > 0
              ? () => openItemEditor(0, 'split')
              : undefined
          }
          readOnly={readOnly}
        />
      )}
      <AttachedComposerPanel
        nodeId={id}
        inputUrls={(items ?? [])
          .filter((it) => it.kind !== 'video')
          .map((it) => it.url)}
        pinned={Boolean(selected)}
        readOnly={readOnly}
      />
      <div className="mh-node-head">
        <div className="mh-node-title">
          {title || t('canvas.mediaNode.title', 'Media')}
        </div>
        {(items?.length ?? 0) > 0 && (
          <button
            type="button"
            data-testid="media-node-add"
            className="nodrag text-[11px] text-canvas-muted transition-colors hover:text-canvas-text disabled:cursor-not-allowed disabled:opacity-50"
            onClick={() => inputRef.current?.click()}
            disabled={readOnly}
          >
            {t('canvas.mediaNode.add', '+ Add')}
          </button>
        )}
      </div>
      <div className="p-2">
        {isEmpty ? (
          <button
            type="button"
            data-testid="media-node-empty"
            onClick={() => inputRef.current?.click()}
            disabled={readOnly}
            className="nodrag flex min-h-[96px] w-full flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-canvas-line text-canvas-muted transition-colors hover:border-[var(--accent-border)] hover:text-canvas-text disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:border-canvas-line disabled:hover:text-canvas-muted"
          >
            <UploadCloud size={18} />
            {/* An empty media node is only its upload invitation, so the
                hint has to stop inviting when the session can't write. */}
            <span className="text-[11px]">
              {readOnly
                ? t('canvas.mediaNode.readOnlyHint', 'Read-only — cannot add media')
                : t('canvas.mediaNode.emptyHint', 'Click or drop images / video')}
            </span>
          </button>
        ) : (
          <div className="grid grid-cols-2 gap-1" data-testid="media-node-grid">
            {(() => {
              let imgSeen = 0;
              let vidSeen = 0;
              return (items ?? []).map((item, i) => {
                const isVideo = item.kind === 'video';
                const kindIndex = isVideo ? vidSeen++ : imgSeen++;
                return (
                  <div
                    key={`${item.url}-${i}`}
                    className="group/cell relative overflow-hidden rounded-md bg-canvas-card"
                  >
                    <button
                      type="button"
                      data-testid={`media-node-thumb-${i}`}
                      className="nodrag block w-full cursor-zoom-in"
                      onClick={() =>
                        setLightbox({ kind: isVideo ? 'video' : 'image', index: kindIndex })
                      }
                    >
                      {isVideo ? (
                        <video
                          data-testid={`media-node-video-${i}`}
                          src={mediaSrc(item.url)}
                          preload="metadata"
                          muted
                          className="aspect-square w-full object-cover"
                        />
                      ) : (
                        <img
                          src={mediaSrc(item.url)}
                          alt={item.name ?? ''}
                          loading="lazy"
                          className="aspect-square w-full object-cover"
                          onLoad={(e) => {
                            const el = e.currentTarget;
                            if (el.naturalWidth) {
                              setResBadges((b) => ({
                                ...b,
                                [i]: `${el.naturalWidth} x ${el.naturalHeight}`,
                              }));
                            }
                          }}
                        />
                      )}
                    </button>
                    {/* IC image-resolution-badge: W×H, hover-revealed. */}
                    {resBadges[i] && (
                      <span
                        data-testid="media-res-badge"
                        className="pointer-events-none absolute bottom-1 left-1 rounded bg-canvas-card/90 px-1 text-[8px] font-semibold text-canvas-muted opacity-0 transition-opacity group-hover/cell:opacity-100"
                      >
                        {resBadges[i]}
                      </span>
                    )}
                    {/* IC mini-x image-delete: hover-revealed per-cell delete. */}
                    {!readOnly && (
                      <button
                        type="button"
                        data-testid="media-item-delete"
                        aria-label="Remove item"
                        title="Remove item"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeItem(i);
                        }}
                        className="nodrag absolute right-1 top-1 flex h-4 w-4 items-center justify-center rounded-full bg-canvas-card/90 text-[9px] text-canvas-muted opacity-0 transition-opacity hover:text-canvas-text group-hover/cell:opacity-100"
                      >
                        ×
                      </button>
                    )}
                  </div>
                );
              });
            })()}
            {Array.from({ length: pendingCells }).map((_, i) => (
              <div
                key={`pending-${i}`}
                data-testid="media-node-pending"
                className="mh-loading-cell flex aspect-square items-center justify-center rounded-md"
              >
                <Loader2 size={14} className="animate-spin text-canvas-muted" />
              </div>
            ))}
          </div>
        )}
        {error && <div className="mt-1.5 text-[11px] text-rose-400">{error}</div>}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={CANVAS_MEDIA_ACCEPT}
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = '';
          if (files.length) void uploadFiles(files);
        }}
      />
      <Handle type="source" position={Position.Right} />
      {lightbox !== null && lightboxItems.length > 0 && (
        <OutputLightbox
          items={lightboxItems}
          index={Math.min(lightbox.index, lightboxItems.length - 1)}
          kind={lightbox.kind}
          onIndexChange={(next) => setLightbox({ kind: lightbox.kind, index: next })}
          onClose={() => setLightbox(null)}
          editActions={
            readOnly || lightbox.kind === 'video'
              ? undefined
              : {
                  crop: () => openItemEditor(lightbox.index, 'crop'),
                  expand: () => openItemEditor(lightbox.index, 'outpaint'),
                  mask: () => openItemEditor(lightbox.index, 'mask'),
                  split: () => openItemEditor(lightbox.index, 'split'),
                }
          }
        />
      )}
      {editState && (
        <MediaItemEditor
          canvasId={canvasId}
          nodeId={id}
          item={editState.item}
          mode={editState.mode}
          onClose={() => setEditState(null)}
          onAppend={(ref) => {
            const current = (useCanvasCoreStore
              .getState()
              .nodes.find((n) => (n as { id?: unknown }).id === id) as
              | { data?: MediaNodeData }
              | undefined)?.data;
            patch({ items: [...(current?.items ?? []), ref] });
          }}
        />
      )}
    </div>
  );
}
