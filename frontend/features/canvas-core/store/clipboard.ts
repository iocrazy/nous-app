/**
 * In-memory clipboard for canvas copy / paste (Phase 1 Week 3).
 *
 * We intentionally do NOT touch the system clipboard for two reasons:
 *
 *   1. Pasting arbitrary external clipboard content into a graph editor
 *      is a footgun — image data, RTF, etc. The "copy nodes / paste
 *      nodes" UX is intra-canvas-only by design (matches Figma, Miro,
 *      Excalidraw).
 *   2. The system clipboard requires user-gesture permission on most
 *      browsers; an in-memory module-level store sidesteps that.
 *
 * Pasted nodes are translated by a small offset and re-id'd so they
 * don't collide with the originals.
 */

import type { CanvasNode } from '../types';

interface ClipboardPayload {
  nodes: CanvasNode[];
}

let payload: ClipboardPayload | null = null;

export function copyNodesToClipboard(nodes: CanvasNode[]): void {
  // Deep-ish clone — JSON copy is fine because nodes are JSONB-shaped
  // pass-through values and React Flow node data has to be JSON-safe
  // anyway.
  payload = { nodes: JSON.parse(JSON.stringify(nodes)) as CanvasNode[] };
}

export function readClipboard(): ClipboardPayload | null {
  return payload ? { nodes: payload.nodes.map((n) => ({ ...n })) } : null;
}

export function clearClipboard(): void {
  payload = null;
}

/**
 * Re-id pasted nodes and nudge them by `PASTE_OFFSET` so they don't sit
 * exactly on top of the originals. Existing IDs are scanned to avoid
 * collisions on repeated pastes.
 */
export const PASTE_OFFSET = 24;

export function preparePastedNodes(
  pasted: CanvasNode[],
  existingIds: Set<string>,
): CanvasNode[] {
  return pasted.map((node) => {
    const obj = node as Record<string, unknown>;
    const next = { ...obj };

    const baseId =
      typeof obj.id === 'string' ? obj.id : `pasted-${Math.floor(Math.random() * 1e9)}`;
    next.id = uniqueId(baseId, existingIds);
    existingIds.add(next.id as string);

    const pos = obj.position;
    if (pos && typeof pos === 'object') {
      const p = pos as { x?: unknown; y?: unknown };
      next.position = {
        x: typeof p.x === 'number' ? p.x + PASTE_OFFSET : PASTE_OFFSET,
        y: typeof p.y === 'number' ? p.y + PASTE_OFFSET : PASTE_OFFSET,
      };
    } else {
      next.position = { x: PASTE_OFFSET, y: PASTE_OFFSET };
    }

    return next as CanvasNode;
  });
}

function uniqueId(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}-${i}`)) i += 1;
  return `${base}-${i}`;
}
