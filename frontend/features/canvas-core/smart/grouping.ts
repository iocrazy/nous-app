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
