// features/canvas-core/smart/grouping.test.ts
// Group container (②-3 — Infinite's group node on React Flow's parentId
// mechanism): grouping wraps the selection in a sized container whose
// children go position-relative; ungrouping releases them back to absolute.

import { describe, expect, it } from 'vitest';

import { applyDropMembership, createEmptyGroup, groupSelection, ungroupNode, releaseChildrenOf } from './grouping';
import type { CanvasNode } from '../types';

const n = (id: string, x: number, y: number, extra: Record<string, unknown> = {}): CanvasNode =>
  ({ id, type: 'shot', position: { x, y }, data: {}, measured: { width: 100, height: 50 }, ...extra }) as unknown as CanvasNode;

describe('groupSelection', () => {
  it('wraps selected nodes in a padded group; children go relative and AFTER the group', () => {
    const nodes = [n('a', 100, 100), n('b', 300, 200), n('c', 900, 900)];
    const result = groupSelection(nodes, ['a', 'b']);
    expect(result).not.toBeNull();
    const { nodes: next, groupId } = result!;

    const group = next.find((x) => (x as { id: string }).id === groupId) as Record<string, unknown>;
    expect(group.type).toBe('group');
    // bbox (100,100)-(400,250) + 24px padding
    expect(group.position).toEqual({ x: 76, y: 76 });
    expect((group.style as { width: number }).width).toBe(348);
    expect((group.style as { height: number }).height).toBe(198);

    const a = next.find((x) => (x as { id: string }).id === 'a') as Record<string, unknown>;
    expect(a.parentId).toBe(groupId);
    expect(a.position).toEqual({ x: 24, y: 24 }); // relative to group
    // React Flow requires parents BEFORE children in the array.
    expect(next.findIndex((x) => (x as { id: string }).id === groupId)).toBeLessThan(
      next.findIndex((x) => (x as { id: string }).id === 'a'),
    );
    // Unselected node untouched.
    const c = next.find((x) => (x as { id: string }).id === 'c') as Record<string, unknown>;
    expect(c.parentId).toBeUndefined();
  });

  it('returns null for fewer than 2 selected nodes', () => {
    expect(groupSelection([n('a', 0, 0)], ['a'])).toBeNull();
  });

  it('does not double-group: already-parented nodes are excluded', () => {
    const base = groupSelection([n('a', 0, 0), n('b', 100, 0)], ['a', 'b'])!;
    const again = groupSelection(base.nodes, ['a', 'b']);
    expect(again).toBeNull();
  });
});

describe('ungroupNode', () => {
  it('releases children to absolute coords and removes the group', () => {
    const grouped = groupSelection([n('a', 100, 100), n('b', 300, 200)], ['a', 'b'])!;
    const next = ungroupNode(grouped.nodes, grouped.groupId);
    expect(next.find((x) => (x as { id: string }).id === grouped.groupId)).toBeUndefined();
    const a = next.find((x) => (x as { id: string }).id === 'a') as Record<string, unknown>;
    expect(a.parentId).toBeUndefined();
    expect(a.position).toEqual({ x: 100, y: 100 }); // back to absolute
  });
});

describe('releaseChildrenOf', () => {
  it('frees children of deleted group ids without touching others', () => {
    const grouped = groupSelection([n('a', 100, 100), n('b', 300, 200)], ['a', 'b'])!;
    const freed = releaseChildrenOf(grouped.nodes, new Set([grouped.groupId]));
    const a = freed.find((x) => (x as { id: string }).id === 'a') as Record<string, unknown>;
    expect(a.parentId).toBeUndefined();
    expect(a.position).toEqual({ x: 100, y: 100 });
    // The group itself is NOT removed here — the delete path does that.
    expect(freed.find((x) => (x as { id: string }).id === grouped.groupId)).toBeTruthy();
  });
});


const grp = (id: string, x: number, y: number, w: number, h: number): CanvasNode =>
  ({ id, type: 'group', position: { x, y }, style: { width: w, height: h }, data: {} }) as unknown as CanvasNode;

describe('createEmptyGroup', () => {
  it('creates a sized empty container at the given position', () => {
    const g = createEmptyGroup({ x: 10, y: 20 }) as unknown as Record<string, unknown>;
    expect(g.type).toBe('group');
    expect(g.position).toEqual({ x: 10, y: 20 });
    expect((g.style as { width: number }).width).toBeGreaterThan(0);
  });
});

describe('applyDropMembership', () => {
  it('absorbs a node whose CENTER lands inside a group (relative pos, appended last)', () => {
    // node a: 100x50 at (120,120) → center (170,145) inside group (100,100,300,200).
    // The group then grows so the child sits ≥24px from every edge: origin
    // shifts to (96,96), so the child's relative position is exactly the pad.
    const nodes = [n('a', 120, 120), grp('g1', 100, 100, 300, 200)];
    const next = applyDropMembership(nodes, 'a')!;
    expect(next).not.toBeNull();
    const g = next.find((x) => (x as { id: string }).id === 'g1') as Record<string, unknown>;
    expect(g.position).toEqual({ x: 96, y: 96 });
    const a = next.find((x) => (x as { id: string }).id === 'a') as Record<string, unknown>;
    expect(a.parentId).toBe('g1');
    expect(a.position).toEqual({ x: 24, y: 24 }); // abs (120,120) - grown origin (96,96)
    // RF ordering: the adopted child comes AFTER its parent.
    const ids = next.map((x) => (x as { id: string }).id);
    expect(ids.indexOf('g1')).toBeLessThan(ids.indexOf('a'));
  });

  it('does nothing when the center misses every group', () => {
    const nodes = [n('a', 900, 900), grp('g1', 100, 100, 300, 200)];
    expect(applyDropMembership(nodes, 'a')).toBeNull();
  });

  it('does nothing when the node stays inside its current parent', () => {
    const nodes = [grp('g1', 100, 100, 300, 200), n('a', 50, 50, { parentId: 'g1' })];
    expect(applyDropMembership(nodes, 'a')).toBeNull();
  });

  it('releases a child dropped on open canvas back to absolute coords', () => {
    // child at relative (500,500) → absolute (600,600), center far outside g1
    const nodes = [grp('g1', 100, 100, 300, 200), n('a', 500, 500, { parentId: 'g1' })];
    const next = applyDropMembership(nodes, 'a')!;
    const a = next.find((x) => (x as { id: string }).id === 'a') as Record<string, unknown>;
    expect(a.parentId).toBeUndefined();
    expect(a.position).toEqual({ x: 600, y: 600 });
  });

  it('re-parents into the topmost (last-painted) overlapping group', () => {
    const nodes = [
      grp('below', 100, 100, 300, 200),
      grp('above', 150, 120, 300, 200),
      n('a', 200, 150),
    ];
    const next = applyDropMembership(nodes, 'a')!;
    const a = next.find((x) => (x as { id: string }).id === 'a') as Record<string, unknown>;
    expect(a.parentId).toBe('above');
  });

  it('grows the group (and compensates children) when the drop pokes past an edge', () => {
    // a: 100x50 at (60,110) → center (110,135) inside g1 but left edge pokes out
    const nodes = [
      grp('g1', 100, 100, 300, 200),
      n('b', 30, 30, { parentId: 'g1' }),
      n('a', 60, 110),
    ];
    const next = applyDropMembership(nodes, 'a')!;
    const g = next.find((x) => (x as { id: string }).id === 'g1') as Record<string, unknown>;
    // left edge grows to a.x - padding = 60-24 = 36; width grows by the shift
    expect((g.position as { x: number }).x).toBe(36);
    expect((g.style as { width: number }).width).toBe(364);
    // existing child b keeps its ABSOLUTE spot: relative x compensated 30→94
    const b = next.find((x) => (x as { id: string }).id === 'b') as Record<string, unknown>;
    expect((b.position as { x: number }).x).toBe(94);
    const a = next.find((x) => (x as { id: string }).id === 'a') as Record<string, unknown>;
    expect((a.position as { x: number }).x).toBe(24); // 60 - new origin 36
  });

  it('never re-parents a group node itself', () => {
    const nodes = [grp('inner', 150, 150, 50, 50), grp('outer', 100, 100, 300, 200)];
    expect(applyDropMembership(nodes, 'inner')).toBeNull();
  });
});
