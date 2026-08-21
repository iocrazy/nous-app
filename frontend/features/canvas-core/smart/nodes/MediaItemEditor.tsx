// features/canvas-core/smart/nodes/MediaItemEditor.tsx
//
// B3 — the media card's edit pipeline. Opens the UnifiedImageEditor on one
// item; derive channels appear only after the item's generated_media row is
// promoted to a resources row (ensureResourceId). Every commit APPENDS the
// product as a new item on the card (non-destructive; brush/resize bake
// client-side and skip the promote entirely). Promote failure degrades
// honestly: only Preview/Brush/Resize stay.

import { useEffect, useState } from 'react';

import {
  deriveCrop,
  deriveGrid,
  deriveMaskCutout,
  deriveOutpaint,
} from '../../services/canvasService';
import { bakeAnnotations, bakeResize } from '../../editor/imageBake';
import { strokesToMaskPngBase64 } from '../../editor/maskExport';
import type { PaintShape } from '../../editor/PaintTool';
import {
  UnifiedImageEditor,
  type EditorMode,
} from '../../editor/UnifiedImageEditor';
import { ensureResourceId } from '../mediaEditBridge';
import {
  importCanvasMedia,
  importResourceAsCanvasMedia,
} from '../mediaImport';
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
  const [resourceId, setResourceId] = useState<string | null>(null);
  const [promoteFailed, setPromoteFailed] = useState(false);
  const [committing, setCommitting] = useState(false);

  useEffect(() => {
    let alive = true;
    setResourceId(null);
    setPromoteFailed(false);
    ensureResourceId(item.url)
      .then((id) => {
        if (alive) setResourceId(id);
      })
      .catch((err) => {
        console.error('[MediaItemEditor] promote failed:', err);
        if (alive) setPromoteFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [item.url]);

  const run = (work: () => Promise<void>) => {
    void (async () => {
      try {
        setCommitting(true);
        await work();
        onClose();
      } catch (err) {
        console.error('[MediaItemEditor] edit failed:', err);
      } finally {
        setCommitting(false);
      }
    })();
  };

  const appendResource = async (resId: string) => {
    const minted = await importResourceAsCanvasMedia(resId);
    onAppend({ url: minted.url, kind: minted.kind, name: item.name });
  };
  const appendBlob = async (blob: Blob, name: string) => {
    const file = new File([blob], name, { type: 'image/png' });
    const minted = await importCanvasMedia(file, canvasId, nodeId);
    onAppend(minted);
  };

  const canDerive = Boolean(resourceId) && !promoteFailed;

  return (
    <UnifiedImageEditor
      open
      src={item.url}
      alt={item.name ?? ''}
      initialMode={mode}
      onClose={onClose}
      committing={committing}
      onCropCommit={
        canDerive
          ? (region) =>
              run(async () => {
                const out = await deriveCrop(resourceId!, region);
                await appendResource(String(out.id));
              })
          : undefined
      }
      onOutpaintCommit={
        canDerive
          ? (padding, prompt) =>
              run(async () => {
                const out = await deriveOutpaint(resourceId!, padding, {
                  prompt,
                });
                await appendResource(String(out.id));
              })
          : undefined
      }
      onMaskCommit={
        canDerive
          ? (strokes, size) =>
              run(async () => {
                const b64 = strokesToMaskPngBase64(
                  strokes,
                  size.width,
                  size.height,
                );
                const out = await deriveMaskCutout(resourceId!, b64);
                await appendResource(String(out.id));
              })
          : undefined
      }
      onSplitCommit={
        canDerive
          ? (lines) =>
              run(async () => {
                const out = await deriveGrid(resourceId!, lines);
                for (const tile of out.tiles) {
                  await appendResource(String(tile.resource.id));
                }
              })
          : undefined
      }
      onBrushCommit={(composite: Blob) =>
        run(async () => {
          await appendBlob(composite, 'brush.png');
        })
      }
      onResizeCommit={(scale: number) =>
        run(async () => {
          const blob = await bakeResize(item.url, scale);
          await appendBlob(blob, 'resized.png');
        })
      }
    />
  );
}
