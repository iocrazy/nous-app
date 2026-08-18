// features/canvas-core/smart/dropCreate.ts
// IC-parity "drop files anywhere" (behavior replicated from Infinite-Canvas,
// zero code ported — license): dropping files on blank canvas creates ONE
// media node centered on the drop point and uploads every file into it; two
// or more same-kind images promote the node to its grid form under the
// title "Group" (that IS Infinite's 多图自动收进分组 — the upload card and
// the visual group are one card in two forms). Paste routes through the
// same path, preferring a selected media node (append) over creating.

import { createMediaNode } from './factories';
import { importCanvasMedia, isImportableCanvasFile } from './mediaImport';
import { SMART_NODE_DEFAULT_WIDTH, type MediaNodeData } from './types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

/** Per-drop file cap — Infinite's observed SMART_UPLOAD_MAX. */
export const CANVAS_DROP_MAX = 20;

function kindOf(file: File): 'image' | 'video' {
  return file.type.startsWith('video/') ? 'video' : 'image';
}

/** Infinite's upload-card naming rule: same-kind plural → Group/Videos,
 *  mixed plural → Media, singles → Image/Video. */
export function titleForDroppedFiles(files: File[]): string {
  if (files.length === 0) return 'Media';
  const kinds = new Set(files.map(kindOf));
  if (kinds.size > 1) return 'Media';
  if (kinds.has('video')) return files.length > 1 ? 'Videos' : 'Video';
  return files.length > 1 ? 'Group' : 'Image';
}

function importableSlice(files: File[]): File[] {
  return files.filter(isImportableCanvasFile).slice(0, CANVAS_DROP_MAX);
}

function mediaDataOf(nodeId: string): MediaNodeData {
  const node = useCanvasCoreStore
    .getState()
    .nodes.find((n) => n.id === nodeId);
  return (node?.data ?? { title: '', items: [] }) as MediaNodeData;
}

/** Sequential per-file upload into an existing media node — shimmer cells
 *  are reserved up-front and settle one by one (same contract as the media
 *  node's own uploader); a failed file releases its cell and the rest land. */
export async function uploadFilesIntoMediaNode(
  nodeId: string,
  files: File[],
): Promise<void> {
  const { patchNode, canvasId } = useCanvasCoreStore.getState();
  if (files.length === 0) return;
  patchNode(nodeId, {
    data: { uploading: (mediaDataOf(nodeId).uploading ?? 0) + files.length },
  });
  for (const file of files) {
    try {
      const item = await importCanvasMedia(file, canvasId, nodeId);
      const current = mediaDataOf(nodeId);
      patchNode(nodeId, {
        data: {
          items: [...(current.items ?? []), item],
          uploading: Math.max(0, (current.uploading ?? 1) - 1),
        },
      });
    } catch (err) {
      console.error('[dropCreate] upload failed:', err);
      const current = mediaDataOf(nodeId);
      patchNode(nodeId, {
        data: { uploading: Math.max(0, (current.uploading ?? 1) - 1) },
      });
    }
  }
}

/** Drop on blank canvas → one media node centered on the drop point. */
export async function createMediaNodeFromFiles(
  files: File[],
  flowPosition: { x: number; y: number },
): Promise<string | null> {
  const importable = importableSlice(files);
  if (importable.length === 0) return null;
  const width = SMART_NODE_DEFAULT_WIDTH.media;
  const node = createMediaNode(
    { title: titleForDroppedFiles(importable), items: [] },
    {
      position: {
        x: Math.round(flowPosition.x - width / 2),
        y: Math.round(flowPosition.y - 60),
      },
    },
  );
  const { nodes, setNodes } = useCanvasCoreStore.getState();
  setNodes([...nodes, node]);
  await uploadFilesIntoMediaNode(node.id, importable);
  return node.id;
}

/** Paste: append to the selected media node when there is one (Infinite's
 *  handleFiles(files, selectedId)), else create at the viewport center. */
export async function pasteFilesToCanvas(
  files: File[],
  viewportCenter: { x: number; y: number },
): Promise<string | null> {
  const importable = importableSlice(files);
  if (importable.length === 0) return null;
  const selectedMedia = useCanvasCoreStore
    .getState()
    .nodes.find(
      (n) => n.type === 'media' && (n as { selected?: boolean }).selected,
    );
  if (selectedMedia) {
    const nodeId = String((selectedMedia as { id: unknown }).id);
    await uploadFilesIntoMediaNode(nodeId, importable);
    return nodeId;
  }
  return createMediaNodeFromFiles(importable, viewportCenter);
}
