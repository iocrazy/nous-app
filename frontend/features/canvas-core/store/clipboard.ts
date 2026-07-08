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
 * A copy captures the selected nodes AND the edges internal to that
 * selection, tagged with the canvas kind. A paste re-ids the nodes, remaps
 * the internal edges onto the new ids, and steps the offset outward on each
 * repeat so successive pastes don't stack.
 */

import type { CanvasConnection, CanvasKind, CanvasNode } from '../types';

interface ClipboardPayload {
  kind: CanvasKind | null;
  nodes: CanvasNode[];
  connections: CanvasConnection[];
}

let payload: ClipboardPayload | null = null;
/** Increments each paste so repeated pastes cascade outward. Reset on copy. */
let pasteGeneration = 0;

export const PASTE_OFFSET = 24;

function idOf(node: CanvasNode): string | null {
  const id = (node as { id?: unknown }).id;
  return typeof id === 'string' ? id : null;
}

/**
 * Copy nodes + the edges internal to that selection (both endpoints copied),
 * tagged with the canvas kind. Deep clone so later mutations can't leak back.
 */
export function copyToClipboard(
  kind: CanvasKind | null,
  nodes: CanvasNode[],
  connections: CanvasConnection[],
): void {
  const ids = new Set(nodes.map(idOf).filter((v): v is string => v !== null));
  const internal = connections.filter(
    (c) => ids.has(String(c.source)) && ids.has(String(c.target)),
  );
  payload = JSON.parse(
    JSON.stringify({ kind, nodes, connections: internal }),
  ) as ClipboardPayload;
  pasteGeneration = 0;
}

export function readClipboard(): ClipboardPayload | null {
  return payload
    ? (JSON.parse(JSON.stringify(payload)) as ClipboardPayload)
    : null;
}

export function clearClipboard(): void {
  payload = null;
  pasteGeneration = 0;
}

export interface PreparedPaste {
  nodes: CanvasNode[];
  connections: CanvasConnection[];
}

/**
 * Clone the clipboard for insertion: fresh unique node ids, internal edges
 * remapped onto those ids (edges whose endpoints didn't come along are
 * dropped), and a cascading offset so a second paste lands further out than
 * the first. Returns null when the clipboard is empty. Every pasted node /
 * edge is deep-cloned so pastes never alias each other or the clipboard.
 */
export function preparePaste(existingIds: Set<string>): PreparedPaste | null {
  if (!payload) return null;
  pasteGeneration += 1;
  const offset = PASTE_OFFSET * pasteGeneration;

  const idMap = new Map<string, string>();
  const nodes = payload.nodes.map((node) => {
    const clone = JSON.parse(JSON.stringify(node)) as Record<string, unknown>;
    const oldId = typeof clone.id === 'string' ? clone.id : null;
    const baseId =
      oldId ?? `pasted-${Math.floor(Math.random() * 1e9)}`;
    const newId = uniqueId(baseId, existingIds);
    existingIds.add(newId);
    if (oldId) idMap.set(oldId, newId);
    clone.id = newId;
    clone.position = offsetPosition(clone.position, offset);
    return clone as CanvasNode;
  });

  const connections: CanvasConnection[] = [];
  for (const c of payload.connections) {
    const source = idMap.get(String(c.source));
    const target = idMap.get(String(c.target));
    if (!source || !target) continue; // an endpoint didn't come along → drop
    const clone = JSON.parse(JSON.stringify(c)) as CanvasConnection;
    connections.push({
      ...clone,
      id: `edge-${crypto.randomUUID()}`,
      source,
      target,
    });
  }

  return { nodes, connections };
}

function offsetPosition(pos: unknown, offset: number): { x: number; y: number } {
  if (pos && typeof pos === 'object') {
    const p = pos as { x?: unknown; y?: unknown };
    return {
      x: typeof p.x === 'number' ? p.x + offset : offset,
      y: typeof p.y === 'number' ? p.y + offset : offset,
    };
  }
  return { x: offset, y: offset };
}

function uniqueId(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}-${i}`)) i += 1;
  return `${base}-${i}`;
}
