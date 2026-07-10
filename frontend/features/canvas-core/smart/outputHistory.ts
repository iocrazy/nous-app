// features/canvas-core/smart/outputHistory.ts
//
// Replace-with-history semantics (Infinite-Canvas parity G4-F2): new
// generation results REPLACE an output node's images[]; whatever was there
// gets archived into a dedicated history node (data.history_for), created
// once below the slot and accumulating newest-first — Infinite's
// replaceOutputsToNodeWithHistory + 历史分组, adapted to smart mode (which
// has no group node type; the archive is an output node holding images[]).
//
// All writes are history-free (patchNode / appendElementsNoHistory):
// generation output is operational state, not a user-undoable edit.

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';
import { createOutputNode } from './factories';
import type { GeneratedImageRef, OutputKind, OutputNodeData } from './types';

const HISTORY_OFFSET_Y = 200;

const asObj = (n: unknown) => n as Record<string, unknown>;

function currentImagesOf(data: OutputNodeData): GeneratedImageRef[] {
  if (Array.isArray(data.images) && data.images.length > 0) return data.images;
  // Legacy single-preview nodes archive that one image.
  if (data.preview_url) {
    return [{ url: data.preview_url, kind: data.kind === 'text' ? 'image' : data.kind }];
  }
  return [];
}

/** Replace an output node's images, archiving the previous set. */
export function replaceOutputImagesWithHistory(
  nodeId: string,
  urls: string[],
  kind: OutputKind,
): void {
  const store = useCanvasCoreStore.getState();
  const node = store.nodes.find((n) => asObj(n).id === nodeId);
  if (!node) return;
  const data = (asObj(node).data ?? {}) as OutputNodeData;

  const previous = currentImagesOf(data);
  if (previous.length > 0) {
    const existingHistory = store.nodes.find((n) => {
      const d = asObj(n).data as OutputNodeData | undefined;
      return d?.history_for === nodeId;
    });
    if (existingHistory) {
      const hData = (asObj(existingHistory).data ?? {}) as OutputNodeData;
      store.patchNode(String(asObj(existingHistory).id), {
        data: { images: [...previous, ...(hData.images ?? [])] },
      });
    } else {
      const pos = (asObj(node).position as { x: number; y: number }) ?? { x: 0, y: 0 };
      const base = createOutputNode(
        { kind, preview_text: 'History', preview_url: null },
        { position: { x: pos.x, y: pos.y + HISTORY_OFFSET_Y } },
      );
      const historyNode = {
        ...base,
        data: {
          ...(base.data as unknown as Record<string, unknown>),
          history_for: nodeId,
          images: previous,
        },
      } as CanvasNode;
      useCanvasCoreStore.getState().appendElementsNoHistory(
        [historyNode],
        [
          {
            id: `edge-${crypto.randomUUID()}`,
            source: nodeId,
            target: String(asObj(historyNode).id),
            sourceHandle: null,
            targetHandle: null,
          },
        ],
      );
    }
  }

  useCanvasCoreStore.getState().patchNode(nodeId, {
    data: {
      kind,
      images: urls.map((url): GeneratedImageRef => ({ url, kind })),
      preview_url: urls[0] ?? null,
      preview_text: '',
    },
  });
}
