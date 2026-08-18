/**
 * MediaNodeView — the smart canvas's Upload card (Infinite's 上传节点).
 *
 * Empty state invites a click / file-drop; uploads stream through
 * POST /generated-media/import so every item is a durable
 * /generated-media/ URL a connected prompt can use as an i2i source.
 * Items render as a 2-col thumbnail grid (images <img>, videos <video>).
 */

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
import { CreateFromNodeBar } from './CreateFromNodeBar';
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
      <CreateFromNodeBar nodeId={id} pinned={Boolean(selected)} readOnly={readOnly} />
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
                    className="overflow-hidden rounded-md bg-canvas-card"
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
                          src={item.url}
                          preload="metadata"
                          muted
                          className="aspect-square w-full object-cover"
                        />
                      ) : (
                        <img
                          src={item.url}
                          alt={item.name ?? ''}
                          loading="lazy"
                          className="aspect-square w-full object-cover"
                        />
                      )}
                    </button>
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
        />
      )}
    </div>
  );
}
