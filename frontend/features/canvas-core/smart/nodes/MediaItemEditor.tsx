// features/canvas-core/smart/nodes/MediaItemEditor.tsx
//
// B3 — the media card's edit pipeline. Opens the UnifiedImageEditor on one
// item; every commit APPENDS the product as a new item on the card
// (non-destructive). Crop / outpaint / split derive server-side from the
// item's OWN url — whatever the image is and wherever it came from — and
// come back as durable generated-media items in the canvas's space; brush /
// mask / resize bake client-side. The server derives need a canvas to file
// the product under, so without one only the client-side tabs show.

import { useState } from 'react';

import {
  deriveCanvasCrop,
  deriveCanvasGrid,
  deriveCanvasOutpaint,
  type CanvasDerivedImage,
} from '../../services/canvasService';
import { bakeResize } from '../../editor/imageBake';
import { strokesToMaskPngBase64 } from '../../editor/maskExport';
import {
  UnifiedImageEditor,
  type EditorMode,
} from '../../editor/UnifiedImageEditor';
import { importCanvasMedia, type CanvasUploadRole } from '../mediaImport';
import type { GeneratedImageRef } from '../types';

export interface MediaItemEditorProps {
  canvasId: string | null;
  nodeId: string;
  item: GeneratedImageRef;
  mode: EditorMode;
  onClose(): void;
  /** Receives every produced item to append onto the card. */
  onAppend(item: GeneratedImageRef): void;
}

export function MediaItemEditor({
  canvasId,
  nodeId,
  item,
  mode,
  onClose,
  onAppend,
}: MediaItemEditorProps) {
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = (work: () => Promise<void>) => {
    void (async () => {
      try {
        setCommitting(true);
        setError(null);
        await work();
        onClose();
      } catch (err) {
        console.error('[MediaItemEditor] edit failed:', err);
        // Stay open with the reason on screen: a refused derive that closed
        // the editor silently is indistinguishable from one that worked.
        setError(err instanceof Error ? err.message : 'Edit failed');
      } finally {
        setCommitting(false);
      }
    })();
  };

  const appendDerived = (image: CanvasDerivedImage) => {
    onAppend({ url: image.url, kind: 'image', name: item.name, id: image.id });
  };
  const appendBlob = async (blob: Blob, name: string, role: CanvasUploadRole) => {
    const file = new File([blob], name, { type: 'image/png' });
    onAppend(await importCanvasMedia(file, canvasId, nodeId, role));
  };

  return (
    <>
      <UnifiedImageEditor
        open
        src={item.url}
        alt={item.name ?? ''}
        initialMode={mode}
        onClose={onClose}
        committing={committing}
        onCropCommit={
          canvasId
            ? (region) =>
                run(async () => {
                  appendDerived(
                    await deriveCanvasCrop(canvasId, item.url, region, { nodeId }),
                  );
                })
            : undefined
        }
        onOutpaintCommit={
          canvasId
            ? (padding, prompt) =>
                run(async () => {
                  appendDerived(
                    await deriveCanvasOutpaint(canvasId, item.url, padding, {
                      nodeId,
                      prompt,
                    }),
                  );
                })
            : undefined
        }
        onSplitCommit={
          canvasId
            ? (lines) =>
                run(async () => {
                  const tiles = await deriveCanvasGrid(canvasId, item.url, lines, {
                    nodeId,
                  });
                  tiles.forEach(appendDerived);
                })
            : undefined
        }
        onMaskCommit={(strokes, size) =>
          run(async () => {
            // IC 生成遮罩节点: the black/white mask itself becomes a new item.
            const b64 = strokesToMaskPngBase64(strokes, size.width, size.height);
            const bin = atob(b64.split(',').pop() ?? b64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            await appendBlob(new Blob([bytes], { type: 'image/png' }), 'mask.png', 'mask');
          })
        }
        onBrushCommit={(composite: Blob) =>
          run(async () => {
            await appendBlob(composite, 'brush.png', 'brush');
          })
        }
        onResizeCommit={(scale: number) =>
          run(async () => {
            const blob = await bakeResize(item.url, scale);
            await appendBlob(blob, 'resized.png', 'derived');
          })
        }
      />
      {error && (
        <div
          data-testid="media-edit-error"
          role="alert"
          className="fixed left-1/2 top-6 z-[60] -translate-x-1/2 rounded bg-danger px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {error}
        </div>
      )}
    </>
  );
}
