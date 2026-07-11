// features/canvas-core/smart/grouping.test.ts
// Group container (②-3 — Infinite's group node on React Flow's parentId
// mechanism): grouping wraps the selection in a sized container whose
// children go position-relative; ungrouping releases them back to absolute.

import { describe, expect, it } from 'vitest';

import { groupSelection, ungroupNode, releaseChildrenOf } from './grouping';
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
