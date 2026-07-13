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

const EMPTY_GROUP_W = 320;
const EMPTY_GROUP_H = 220;

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
    const adopted = {
      ...(dragged as object),
      parentId: hitId,
      position: { x: abs.x - hitPos.x, y: abs.y - hitPos.y },
    } as CanvasNode;
    // RF requires parents before children: re-append the dragged node after
    // everything (also puts it on top inside the container).
    const rest = nodes.filter((n) => String(asObj(n).id) !== draggedId);
    return fitGroupToRect([...rest, adopted], hitId, { x: abs.x, y: abs.y, w, h });
  }

  // Dropped on open canvas while parented → release to absolute coords.
  const { parentId: _drop, ...restFields } = dragged as unknown as Record<string, unknown>;
  const released = { ...restFields, position: abs } as CanvasNode;
  return nodes.map((n) => (String(asObj(n).id) === draggedId ? released : n));
}
