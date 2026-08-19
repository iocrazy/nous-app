// features/canvas-core/smart/grouping.test.ts
// Group container (②-3 — Infinite's group node on React Flow's parentId
// mechanism): grouping wraps the selection in a sized container whose
// children go position-relative; ungrouping releases them back to absolute.

import { describe, expect, it } from 'vitest';

import { absorbImagesOnConnect, groupPreviewItems, groupSummary, absorbMediaIntoGroup, applyDropMembership, arrangeGroupChildren, createEmptyGroup, groupSelection, hitGroupIdFor, ungroupNode, releaseChildrenOf } from './grouping';
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

describe('applyDropMembership strips RF-internal fields', () => {
  it('adopted and released nodes never keep measured/width/height snapshots', () => {
    const dirty = {
      ...n('a', 120, 120),
      width: 280,
      height: 120,
      selected: true,
      dragging: false,
    } as unknown as CanvasNode;
    const absorbed = applyDropMembership([dirty, grp('g1', 100, 100, 300, 200)], 'a')!;
    const a = absorbed.find((x) => (x as { id: string }).id === 'a') as Record<string, unknown>;
    expect(a.measured).toBeUndefined();
    expect(a.width).toBeUndefined();
    expect(a.selected).toBeUndefined();

    const child = {
      ...n('b', 500, 500, { parentId: 'g1' }),
      width: 280,
      height: 120,
    } as unknown as CanvasNode;
    const released = applyDropMembership([grp('g1', 100, 100, 300, 200), child], 'b')!;
    const b = released.find((x) => (x as { id: string }).id === 'b') as Record<string, unknown>;
    expect(b.width).toBeUndefined();
    expect(b.measured).toBeUndefined();
  });
});

const mediaNode = (id: string, x: number, y: number, urls: string[]): CanvasNode =>
  ({
    id,
    type: 'media',
    position: { x, y },
    data: { title: 'Media', items: urls.map((url) => ({ url, kind: 'image' })) },
    measured: { width: 100, height: 50 },
  }) as unknown as CanvasNode;

describe('absorbMediaIntoGroup (group v2 — IC smart-group)', () => {
  const conn = (id: string, source: string, target: string) => ({
    id,
    source,
    target,
    sourceHandle: null,
    targetHandle: null,
  });

  it('merges items (deduped), removes the media node, re-routes wires to the group', () => {
    const nodes = [
      grp('g1', 100, 100, 300, 200),
      mediaNode('m1', 120, 120, ['/api/v1/generated-media/1/cover', '/api/v1/generated-media/2/cover']),
      n('p1', 600, 100, { type: 'prompt' }),
    ];
    (nodes[0] as unknown as { data: Record<string, unknown> }).data = {
      label: 'G',
      items: [{ url: '/api/v1/generated-media/2/cover', kind: 'image' }],
    };
    const res = absorbMediaIntoGroup(
      nodes,
      [conn('e1', 'm1', 'p1')],
      'm1',
      'g1',
    )!;
    expect(res).not.toBeNull();
    const ids = res.nodes.map((x) => (x as { id: string }).id);
    expect(ids).not.toContain('m1');
    const g = res.nodes.find((x) => (x as { id: string }).id === 'g1') as {
      data: { items: Array<{ url: string }> };
    };
    expect(g.data.items.map((i) => i.url)).toEqual([
      '/api/v1/generated-media/2/cover',
      '/api/v1/generated-media/1/cover',
    ]);
    expect(res.connections).toEqual([
      expect.objectContaining({ source: 'g1', target: 'p1' }),
    ]);
  });

  it('dedupes a re-routed wire that would duplicate an existing group wire', () => {
    const nodes = [grp('g1', 100, 100, 300, 200), mediaNode('m1', 120, 120, []), n('p1', 600, 100)];
    const res = absorbMediaIntoGroup(
      nodes,
      [conn('e1', 'g1', 'p1'), conn('e2', 'm1', 'p1')],
      'm1',
      'g1',
    )!;
    expect(res.connections).toHaveLength(1);
  });

  it('grows a too-short group to fit the grid', () => {
    const urls = Array.from({ length: 8 }, (_, i) => `/api/v1/generated-media/${i}/cover`);
    const nodes = [grp('g1', 100, 100, 300, 60), mediaNode('m1', 120, 110, urls)];
    const res = absorbMediaIntoGroup(nodes, [], 'm1', 'g1')!;
    const g = res.nodes.find((x) => (x as { id: string }).id === 'g1') as Record<string, unknown>;
    expect((g.style as { height: number }).height).toBeGreaterThan(60);
  });

  it('returns null for a non-media node', () => {
    const nodes = [grp('g1', 100, 100, 300, 200), n('a', 120, 120)];
    expect(absorbMediaIntoGroup(nodes, [], 'a', 'g1')).toBeNull();
  });
});

describe('hitGroupIdFor', () => {
  it('matches applyDropMembership center-point semantics', () => {
    const nodes = [mediaNode('m1', 120, 120, []), grp('g1', 100, 100, 300, 200)];
    expect(hitGroupIdFor(nodes, 'm1')).toBe('g1');
    const far = [mediaNode('m2', 900, 900, []), grp('g1', 100, 100, 300, 200)];
    expect(hitGroupIdFor(far, 'm2')).toBeNull();
  });
});

import { gridColsFor, groupGridHeight, groupGridWidth } from './grouping';

describe('adaptive thumbnail grid geometry (IC smartGroupThumbLayout)', () => {
  it('columns: 1 for a single, then min 2 / max 4 by ceil(sqrt(n))', () => {
    expect(gridColsFor(1)).toBe(1);
    expect(gridColsFor(2)).toBe(2);
    expect(gridColsFor(3)).toBe(2);
    expect(gridColsFor(4)).toBe(2);
    expect(gridColsFor(5)).toBe(3);
    expect(gridColsFor(9)).toBe(3);
    expect(gridColsFor(10)).toBe(4);
    expect(gridColsFor(50)).toBe(4);
  });

  it('width/height grow with the item count', () => {
    expect(groupGridWidth(8)).toBeGreaterThan(groupGridWidth(2));
    expect(groupGridHeight(8)).toBeGreaterThan(groupGridHeight(2));
  });
});


// ---- arrangeGroupChildren (IC 整理排列) -----------------------------------

describe('arrangeGroupChildren', () => {
  const group = {
    id: 'g1',
    type: 'group',
    position: { x: 100, y: 100 },
    style: { width: 300, height: 200 },
    data: {},
  } as unknown as CanvasNode;

  function child(id: string, x: number, y: number): CanvasNode {
    return {
      id,
      type: 'media',
      parentId: 'g1',
      position: { x, y },
      measured: { width: 100, height: 80 },
      data: {},
    } as unknown as CanvasNode;
  }

  it('lays members on a grid and grows the container to fit', () => {
    const nodes = [group, child('a', 900, 900), child('b', -50, 40), child('c', 10, 500)];
    const out = arrangeGroupChildren(nodes, 'g1');
    const byId = new Map(out.map((n) => [(n as { id: string }).id, n]));
    const a = byId.get('a') as { position: { x: number; y: number } };
    const b = byId.get('b') as { position: { x: number; y: number } };
    const c = byId.get('c') as { position: { x: number; y: number } };
    // 3 members → 2 cols: a,b on row 1, c on row 2; all inside the padding.
    expect(a.position.y).toBe(b.position.y);
    expect(c.position.y).toBeGreaterThan(a.position.y);
    expect(b.position.x).toBeGreaterThan(a.position.x);
    expect(a.position.x).toBeGreaterThan(0);
    expect(a.position.y).toBeGreaterThan(0);
    const g = byId.get('g1') as { style: { width: number; height: number } };
    expect(g.style.width).toBeGreaterThanOrEqual(2 * 100);
    expect(g.style.height).toBeGreaterThanOrEqual(2 * 80);
    // Group's own position is untouched — arrange is internal.
    expect((byId.get('g1') as { position: { x: number } }).position.x).toBe(100);
  });

  it('no members → unchanged', () => {
    const nodes = [group];
    expect(arrangeGroupChildren(nodes, 'g1')).toBe(nodes);
  });

  it('non-members keep their positions', () => {
    const loose = {
      id: 'z',
      type: 'media',
      position: { x: 7, y: 8 },
      data: {},
    } as unknown as CanvasNode;
    const out = arrangeGroupChildren([group, child('a', 0, 0), loose], 'g1');
    const z = out.find((n) => (n as { id: string }).id === 'z') as {
      position: { x: number; y: number };
    };
    expect(z.position).toEqual({ x: 7, y: 8 });
  });
});


// ---- absorbImagesOnConnect (IC parity ⑦ — wiring INTO a group collects) --

describe('absorbImagesOnConnect', () => {
  const groupNode = {
    id: 'g1',
    type: 'group',
    position: { x: 0, y: 0 },
    data: { label: '', items: [{ url: '/api/v1/generated-media/old.png', kind: 'image' }] },
  } as unknown as CanvasNode;
  const mediaNode = {
    id: 'm1',
    type: 'media',
    position: { x: 0, y: 0 },
    data: {
      title: 'Media',
      items: [
        { url: '/api/v1/generated-media/a.png', kind: 'image', name: 'a' },
        { url: '/api/v1/generated-media/old.png', kind: 'image', name: 'dup' },
      ],
    },
  } as unknown as CanvasNode;

  it('merges the source images into the group, deduped by url', () => {
    const out = absorbImagesOnConnect([groupNode, mediaNode], 'm1', 'g1');
    const g = out.find((n) => (n as { id: string }).id === 'g1') as {
      data: { items: Array<{ url: string }> };
    };
    expect(g.data.items.map((i) => i.url)).toEqual([
      '/api/v1/generated-media/old.png',
      '/api/v1/generated-media/a.png',
    ]);
  });

  it('non-group target → unchanged identity', () => {
    const nodes = [groupNode, mediaNode];
    expect(absorbImagesOnConnect(nodes, 'm1', 'm1')).toBe(nodes);
  });
});


// ---- groupSummary + groupPreviewItems (IC D — 分组摘要与整组预览) --------

describe('groupSummary', () => {
  it('counts member prompts/loops and images (items + member images)', () => {
    const nodes = [
      { id: 'g1', type: 'group', position: { x: 0, y: 0 }, data: { items: [{ url: '/api/v1/generated-media/1/file', kind: 'image' }] } },
      { id: 'p1', type: 'prompt', parentId: 'g1', position: { x: 0, y: 0 }, data: {} },
      { id: 'l1', type: 'loop', parentId: 'g1', position: { x: 0, y: 0 }, data: {} },
      { id: 'm1', type: 'media', parentId: 'g1', position: { x: 0, y: 0 }, data: { items: [{ url: '/api/v1/generated-media/2/file', kind: 'image' }, { url: '/api/v1/generated-media/3/file', kind: 'video' }] } },
      { id: 'z', type: 'media', position: { x: 0, y: 0 }, data: { items: [] } },
    ] as unknown as CanvasNode[];
    expect(groupSummary(nodes, 'g1')).toEqual({ prompts: 1, loops: 1, images: 2 });
  });
});

describe('groupPreviewItems', () => {
  it('collects the group grid plus member images, deduped', () => {
    const nodes = [
      { id: 'g1', type: 'group', position: { x: 0, y: 0 }, data: { items: [{ url: '/api/v1/generated-media/1/file', kind: 'image', name: 'a' }] } },
      { id: 'm1', type: 'media', parentId: 'g1', position: { x: 0, y: 0 }, data: { items: [{ url: '/api/v1/generated-media/1/file', kind: 'image' }, { url: '/api/v1/generated-media/2/file', kind: 'image', name: 'b' }] } },
      { id: 'o1', type: 'output', parentId: 'g1', position: { x: 0, y: 0 }, data: { images: [{ url: '/api/v1/generated-media/3/file', kind: 'image' }] } },
    ] as unknown as CanvasNode[];
    expect(groupPreviewItems(nodes, 'g1').map((i) => i.url)).toEqual([
      '/api/v1/generated-media/1/file',
      '/api/v1/generated-media/2/file',
      '/api/v1/generated-media/3/file',
    ]);
  });
});
