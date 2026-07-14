// features/canvas-core/smart/grouping.ts
//
// Group container (②-3 — Infinite's group node, realised on React Flow's
// native parentId mechanism so drag-moves-members comes from the engine):
//
//   group    — wrap the selection in a padded container node; members'
//              positions become container-relative and the container is
//              inserted BEFORE them (RF requires parents first).
//   ungroup  — release members back to absolute coords, drop the container.
//
// Pure functions over the store's node array; callers commit via setNodes
// (user edits — undoable).

import type { CanvasNode } from '../types';
import { stripRfInternals } from '../store/canvasCoreStore';

const GROUP_PADDING = 24;
const FALLBACK_W = 240;
const FALLBACK_H = 120;

const asObj = (n: unknown) => n as Record<string, unknown>;

function sizeOf(node: CanvasNode): { width: number; height: number } {
  const measured = asObj(node).measured as { width?: number; height?: number } | undefined;
  return {
    width: measured?.width ?? FALLBACK_W,
    height: measured?.height ?? FALLBACK_H,
  };
}

export interface GroupResult {
  nodes: CanvasNode[];
  groupId: string;
}

/** Wrap ≥2 selected, not-already-parented nodes in a group container. */
export function groupSelection(
  nodes: CanvasNode[],
  selection: string[],
): GroupResult | null {
  const selected = new Set(selection);
  const members = nodes.filter((n) => {
    const obj = asObj(n);
    return (
      selected.has(String(obj.id)) &&
      obj.parentId == null &&
      obj.type !== 'group'
    );
  });
  if (members.length < 2) return null;

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const m of members) {
    const pos = asObj(m).position as { x: number; y: number };
    const { width, height } = sizeOf(m);
    minX = Math.min(minX, pos.x);
    minY = Math.min(minY, pos.y);
    maxX = Math.max(maxX, pos.x + width);
    maxY = Math.max(maxY, pos.y + height);
  }

  const groupId = `group-${crypto.randomUUID()}`;
  const group = {
    id: groupId,
    type: 'group',
    position: { x: minX - GROUP_PADDING, y: minY - GROUP_PADDING },
    style: {
      width: maxX - minX + GROUP_PADDING * 2,
      height: maxY - minY + GROUP_PADDING * 2,
    },
    data: { label: 'Group' },
  } as unknown as CanvasNode;

  const memberIds = new Set(members.map((m) => String(asObj(m).id)));
  const rest: CanvasNode[] = [];
  const reparented: CanvasNode[] = [];
  for (const n of nodes) {
    const obj = asObj(n);
    if (!memberIds.has(String(obj.id))) {
      rest.push(n);
      continue;
    }
    const pos = obj.position as { x: number; y: number };
    reparented.push({
      ...(n as object),
      parentId: groupId,
      position: {
        x: pos.x - (minX - GROUP_PADDING),
        y: pos.y - (minY - GROUP_PADDING),
      },
    } as CanvasNode);
  }
  return { nodes: [...rest, group, ...reparented], groupId };
}

function absolutePositionOf(child: CanvasNode, group: CanvasNode): { x: number; y: number } {
  const cp = asObj(child).position as { x: number; y: number };
  const gp = asObj(group).position as { x: number; y: number };
  return { x: cp.x + gp.x, y: cp.y + gp.y };
}

/** Release a group's children to absolute coords and drop the container. */
export function ungroupNode(nodes: CanvasNode[], groupId: string): CanvasNode[] {
  const group = nodes.find((n) => String(asObj(n).id) === groupId);
  if (!group) return nodes;
  return nodes
    .filter((n) => String(asObj(n).id) !== groupId)
    .map((n) => {
      if (asObj(n).parentId !== groupId) return n;
      const { parentId: _drop, ...rest } = n as unknown as Record<string, unknown>;
      return { ...rest, position: absolutePositionOf(n, group) } as CanvasNode;
    });
}

/** Free the children of soon-to-be-deleted groups (delete path — the
 *  caller removes the groups themselves). */
export function releaseChildrenOf(
  nodes: CanvasNode[],
  groupIds: ReadonlySet<string>,
): CanvasNode[] {
  const groupsById = new Map(
    nodes
      .filter((n) => groupIds.has(String(asObj(n).id)))
      .map((n) => [String(asObj(n).id), n]),
  );
  return nodes.map((n) => {
    const parentId = asObj(n).parentId;
    if (typeof parentId !== 'string' || !groupsById.has(parentId)) return n;
    const group = groupsById.get(parentId)!;
    const { parentId: _drop, ...rest } = n as unknown as Record<string, unknown>;
    return { ...rest, position: absolutePositionOf(n, group) } as CanvasNode;
  });
}

// ---- Drop membership (IC parity 1b — Infinite's 拖入自动收进分组) --------

const EMPTY_GROUP_W = 220;
const EMPTY_GROUP_H = 150;

export interface NodeSize {
  width: number;
  height: number;
}

/** An empty group container placed from the create menu (Infinite's 分组
 *  card) — members arrive later by dragging nodes onto it. */
export function createEmptyGroup(position: { x: number; y: number }): CanvasNode {
  return {
    id: `group-${crypto.randomUUID()}`,
    type: 'group',
    position,
    style: { width: EMPTY_GROUP_W, height: EMPTY_GROUP_H },
    data: { label: 'Group' },
  } as unknown as CanvasNode;
}

function rectOfGroup(group: CanvasNode): { x: number; y: number; w: number; h: number } {
  const obj = asObj(group);
  const pos = obj.position as { x: number; y: number };
  const style = obj.style as { width?: number; height?: number } | undefined;
  return {
    x: pos.x,
    y: pos.y,
    w: style?.width ?? EMPTY_GROUP_W,
    h: style?.height ?? EMPTY_GROUP_H,
  };
}

/** Grow a group (in-place on a cloned array) so `childAbs` sits inside it
 *  with padding. A grow toward the top/left shifts the container origin, so
 *  every existing child's container-relative position is compensated. */
function fitGroupToRect(
  nodes: CanvasNode[],
  groupId: string,
  childAbs: { x: number; y: number; w: number; h: number },
): CanvasNode[] {
  const group = nodes.find((n) => String(asObj(n).id) === groupId);
  if (!group) return nodes;
  const g = rectOfGroup(group);
  const minX = Math.min(g.x, childAbs.x - GROUP_PADDING);
  const minY = Math.min(g.y, childAbs.y - GROUP_PADDING);
  const maxX = Math.max(g.x + g.w, childAbs.x + childAbs.w + GROUP_PADDING);
  const maxY = Math.max(g.y + g.h, childAbs.y + childAbs.h + GROUP_PADDING);
  if (minX === g.x && minY === g.y && maxX === g.x + g.w && maxY === g.y + g.h) {
    return nodes;
  }
  const dx = g.x - minX;
  const dy = g.y - minY;
  return nodes.map((n) => {
    const obj = asObj(n);
    if (String(obj.id) === groupId) {
      return {
        ...(n as object),
        position: { x: minX, y: minY },
        style: {
          ...((obj.style as object) ?? {}),
          width: maxX - minX,
          height: maxY - minY,
        },
      } as CanvasNode;
    }
    if (obj.parentId === groupId && (dx !== 0 || dy !== 0)) {
      const pos = obj.position as { x: number; y: number };
      return {
        ...(n as object),
        position: { x: pos.x + dx, y: pos.y + dy },
      } as CanvasNode;
    }
    return n;
  });
}

/**
 * Membership change for a solo node drop (Infinite's center-point rule):
 * the dragged node's CENTER inside a group's rect absorbs it (topmost
 * group wins); a child whose center left its parent is released back to
 * absolute coords. Returns the new node array, or null when nothing
 * changes. Groups themselves never re-parent (group-into-group deferred).
 */
/** Center-point group hit (Infinite's rule): the dragged node's center
 *  against every group rect; topmost (last-painted) hit wins. Exported so
 *  the media-absorb path shares the exact same test. */
export function hitGroupIdFor(
  nodes: CanvasNode[],
  draggedId: string,
  sizes?: ReadonlyMap<string, Partial<NodeSize>>,
): string | null {
  const dragged = nodes.find((n) => String(asObj(n).id) === draggedId);
  if (!dragged) return null;
  const measured = sizes?.get(draggedId);
  const fallback = sizeOf(dragged);
  const w = measured?.width ?? fallback.width;
  const h = measured?.height ?? fallback.height;
  const parentId =
    typeof asObj(dragged).parentId === 'string'
      ? (asObj(dragged).parentId as string)
      : null;
  const parent = parentId
    ? nodes.find((n) => String(asObj(n).id) === parentId)
    : undefined;
  const pos = asObj(dragged).position as { x: number; y: number };
  const parentPos = parent
    ? (asObj(parent).position as { x: number; y: number })
    : { x: 0, y: 0 };
  const center = {
    x: pos.x + parentPos.x + w / 2,
    y: pos.y + parentPos.y + h / 2,
  };
  let hitId: string | null = null;
  for (const n of nodes) {
    const obj = asObj(n);
    if (obj.type !== 'group' || String(obj.id) === draggedId) continue;
    const r = rectOfGroup(n);
    if (
      center.x >= r.x &&
      center.x <= r.x + r.w &&
      center.y >= r.y &&
      center.y <= r.y + r.h
    ) {
      hitId = String(obj.id);
    }
  }
  return hitId;
}

/** Absorb a media node into a group (IC's absorbImageNodeIntoSmartGroup,
 *  group v2): its items merge into the group grid (deduped by url), wires
 *  FROM the media node re-route to the group (deduped), the media node is
 *  removed, and the group grows tall enough to show the grid. */
export function absorbMediaIntoGroup(
  nodes: CanvasNode[],
  connections: Array<Record<string, unknown>>,
  mediaId: string,
  groupId: string,
): { nodes: CanvasNode[]; connections: Array<Record<string, unknown>> } | null {
  const media = nodes.find((n) => String(asObj(n).id) === mediaId);
  const group = nodes.find((n) => String(asObj(n).id) === groupId);
  if (!media || !group || asObj(media).type !== 'media') return null;

  const mediaItems = (
    ((asObj(media).data ?? {}) as { items?: Array<{ url: string }> }).items ?? []
  ) as Array<Record<string, unknown>>;
  const groupData = (asObj(group).data ?? {}) as Record<string, unknown>;
  const existing = (groupData.items ?? []) as Array<Record<string, unknown>>;
  const seen = new Set(existing.map((i) => String(i.url)));
  const merged = [
    ...existing,
    ...mediaItems.filter((i) => !seen.has(String(i.url))),
  ];

  // Grow the group to fit the adaptive thumbnail grid (IC parity): never
  // shrink below the user's current box, but ensure the grid fits.
  const minH = groupGridHeight(merged.length);
  const minW = groupGridWidth(merged.length);
  const g = rectOfGroup(group);

  const nextNodes = nodes
    .filter((n) => String(asObj(n).id) !== mediaId)
    .map((n) => {
      if (String(asObj(n).id) !== groupId) return n;
      return {
        ...(n as object),
        data: { ...groupData, items: merged },
        style: {
          ...((asObj(n).style as object) ?? {}),
          width: Math.max(g.w, minW),
          height: Math.max(g.h, minH),
        },
      } as CanvasNode;
    });

  const dedup = new Set<string>();
  const nextConnections: Array<Record<string, unknown>> = [];
  for (const c of connections) {
    if (String(c.target) === mediaId) continue; // media cards take no wires
    const rerouted =
      String(c.source) === mediaId ? { ...c, source: groupId } : c;
    const key = `${rerouted.source}→${rerouted.target}:${rerouted.sourceHandle ?? ''}:${rerouted.targetHandle ?? ''}`;
    if (dedup.has(key)) continue;
    dedup.add(key);
    nextConnections.push(rerouted);
  }
  return { nodes: nextNodes, connections: nextConnections };
}

// Thumbnail-grid geometry, IC parity (smartGroupThumbLayout js:1456).
export const GRID_CELL = 44;
export const GRID_GAP = 6;
export const GRID_MAX = 12;
export const GROUP_OUTER_PAD = 16;
export const GROUP_HEADER = 28;

/** IC's adaptive column count: min 2, max 4, ceil(sqrt(n)). */
export function gridColsFor(count: number): number {
  if (count <= 1) return 1;
  return Math.min(4, Math.max(2, Math.ceil(Math.sqrt(count))));
}

/** Group box height that fits `count` thumbs at the adaptive column count. */
export function groupGridHeight(count: number): number {
  const shown = Math.min(count, GRID_MAX);
  const cols = gridColsFor(shown);
  const rows = Math.max(1, Math.ceil(shown / cols));
  return GROUP_HEADER + rows * (GRID_CELL + GRID_GAP) + GROUP_OUTER_PAD;
}

/** Group box width that fits the adaptive column count. */
export function groupGridWidth(count: number): number {
  const shown = Math.min(count, GRID_MAX);
  const cols = gridColsFor(shown);
  return GROUP_OUTER_PAD * 2 + cols * GRID_CELL + (cols - 1) * GRID_GAP;
}

export function applyDropMembership(
  nodes: CanvasNode[],
  draggedId: string,
  sizes?: ReadonlyMap<string, Partial<NodeSize>>,
): CanvasNode[] | null {
  const dragged = nodes.find((n) => String(asObj(n).id) === draggedId);
  if (!dragged || asObj(dragged).type === 'group') return null;

  const measured = sizes?.get(draggedId);
  const fallback = sizeOf(dragged);
  const w = measured?.width ?? fallback.width;
  const h = measured?.height ?? fallback.height;

  const parentId =
    typeof asObj(dragged).parentId === 'string'
      ? (asObj(dragged).parentId as string)
      : null;
  const parent = parentId
    ? nodes.find((n) => String(asObj(n).id) === parentId)
    : undefined;
  const pos = asObj(dragged).position as { x: number; y: number };
  const parentPos = parent
    ? (asObj(parent).position as { x: number; y: number })
    : { x: 0, y: 0 };
  const abs = { x: pos.x + parentPos.x, y: pos.y + parentPos.y };
  const center = { x: abs.x + w / 2, y: abs.y + h / 2 };

  // Topmost hit = LAST in array order (React Flow paints later nodes above).
  let hit: CanvasNode | undefined;
  for (const n of nodes) {
    const obj = asObj(n);
    if (obj.type !== 'group' || String(obj.id) === draggedId) continue;
    const r = rectOfGroup(n);
    if (
      center.x >= r.x &&
      center.x <= r.x + r.w &&
      center.y >= r.y &&
      center.y <= r.y + r.h
    ) {
      hit = n;
    }
  }

  const hitId = hit ? String(asObj(hit).id) : null;
  if (hitId === parentId) return null; // still inside the same parent (or none)

  if (hit && hitId) {
    const hitPos = asObj(hit).position as { x: number; y: number };
    const adopted = stripRfInternals({
      ...(dragged as object),
      parentId: hitId,
      position: { x: abs.x - hitPos.x, y: abs.y - hitPos.y },
    } as CanvasNode);
    // RF requires parents before children: re-append the dragged node after
    // everything (also puts it on top inside the container).
    const rest = nodes.filter((n) => String(asObj(n).id) !== draggedId);
    return fitGroupToRect([...rest, adopted], hitId, { x: abs.x, y: abs.y, w, h });
  }

  // Dropped on open canvas while parented → release to absolute coords.
  const { parentId: _drop, ...restFields } = dragged as unknown as Record<string, unknown>;
  const released = stripRfInternals({ ...restFields, position: abs } as CanvasNode);
  return nodes.map((n) => (String(asObj(n).id) === draggedId ? released : n));
}
