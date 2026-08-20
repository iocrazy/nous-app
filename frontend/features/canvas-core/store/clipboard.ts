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
 * Clone a subgraph for insertion: fresh unique node ids, internal edges
 * remapped onto those ids (edges whose endpoints didn't come along are
 * dropped), smart data tags re-pointed or stripped, and every node / edge
 * deep-cloned so clones never alias their source. Shared by paste (from the
 * clipboard payload) and duplicate (straight from the live selection, G6).
 */
export function cloneSubgraph(
  sourceNodes: CanvasNode[],
  sourceConnections: CanvasConnection[],
  existingIds: Set<string>,
  offset: number,
): PreparedPaste {
  const idMap = new Map<string, string>();
  const nodes = sourceNodes.map((node) => {
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

  // Second pass (idMap is complete now): smart data tags reference OTHER
  // node ids — left untouched, a cloned gen slot would STEAL the original
  // prompt's future results (upsertGenerationSlots matches the first tag).
  // Re-point tags whose referent came along; strip the rest. parentId
  // (group membership, ②-3) gets the same treatment at the top level.
  for (const node of nodes) {
    const obj = node as Record<string, unknown>;
    if (typeof obj.parentId === 'string') {
      const mapped = idMap.get(obj.parentId);
      if (mapped) obj.parentId = mapped;
      else delete obj.parentId;
    }
    remapSmartTags(obj, idMap);
  }

  const connections: CanvasConnection[] = [];
  for (const c of sourceConnections) {
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

/** Smart-mode data tags that point at other nodes by id. */
function remapSmartTags(
  node: Record<string, unknown>,
  idMap: Map<string, string>,
): void {
  const data = node.data as
    | {
        gen_slot?: { node_id?: string };
        loop_slot?: { loop_id?: string };
        history_for?: string;
      }
    | undefined;
  if (!data || typeof data !== 'object') return;
  if (data.gen_slot) {
    const mapped = idMap.get(String(data.gen_slot.node_id));
    if (mapped) data.gen_slot.node_id = mapped;
    else delete data.gen_slot;
  }
  if (data.loop_slot) {
    const mapped = idMap.get(String(data.loop_slot.loop_id));
    if (mapped) data.loop_slot.loop_id = mapped;
    else delete data.loop_slot;
  }
  if (typeof data.history_for === 'string') {
    const mapped = idMap.get(data.history_for);
    if (mapped) data.history_for = mapped;
    else delete data.history_for;
  }
}

/**
 * Clone the clipboard for insertion with a cascading offset so a second
 * paste lands further out than the first. Null when the clipboard is empty.
 */
export function preparePaste(
  existingIds: Set<string>,
  options: { at?: { x: number; y: number } } = {},
): PreparedPaste | null {
  if (!payload) return null;
  if (options.at) {
    // IC pasteNodes: the subgraph's bounding-box centre lands on the
    // pointer's world position, so repeat pastes follow the mouse instead
    // of stacking on a cascading offset.
    const centre = bboxCentre(payload.nodes);
    const cloned = cloneSubgraph(payload.nodes, payload.connections, existingIds, 0);
    const dx = options.at.x - centre.x;
    const dy = options.at.y - centre.y;
    for (const node of cloned.nodes) {
      const obj = node as Record<string, unknown>;
      const pos = obj.position as { x: number; y: number } | undefined;
      if (pos) obj.position = { x: pos.x + dx, y: pos.y + dy };
    }
    return cloned;
  }
  pasteGeneration += 1;
  return cloneSubgraph(
    payload.nodes,
    payload.connections,
    existingIds,
    PASTE_OFFSET * pasteGeneration,
  );
}

function bboxCentre(nodes: CanvasNode[]): { x: number; y: number } {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes) {
    const pos = (n as Record<string, unknown>).position as
      | { x: number; y: number }
      | undefined;
    if (!pos) continue;
    minX = Math.min(minX, pos.x); minY = Math.min(minY, pos.y);
    maxX = Math.max(maxX, pos.x); maxY = Math.max(maxY, pos.y);
  }
  if (minX === Infinity) return { x: 0, y: 0 };
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2 };
}

/**
 * Clone the live selection for duplication (Cmd/Ctrl+D — Infinite's
 * alt-drag-copy): same re-id/remap machinery, fixed one-step offset, and
 * the user's copy/paste clipboard is left untouched.
 */
export function prepareDuplicate(
  selectedNodes: CanvasNode[],
  allConnections: CanvasConnection[],
  existingIds: Set<string>,
): PreparedPaste | null {
  if (selectedNodes.length === 0) return null;
  const ids = new Set(
    selectedNodes.map(idOf).filter((v): v is string => v !== null),
  );
  const internal = allConnections.filter(
    (c) => ids.has(String(c.source)) && ids.has(String(c.target)),
  );
  return cloneSubgraph(selectedNodes, internal, existingIds, PASTE_OFFSET);
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
