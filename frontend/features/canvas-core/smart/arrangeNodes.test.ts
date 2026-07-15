/**
 * arrangeSelected — shared IC 整理选中 layout. With 2+ selected top-level
 * nodes, only that subset moves; group children ride along untouched.
 */
import { describe, expect, it } from 'vitest';
import { arrangeSelected, canArrangeSelection } from './arrangeNodes';
import type { CanvasConnection, CanvasNode } from '../types';

const n = (id: string, x: number, y: number, extra: Record<string, unknown> = {}): CanvasNode =>
  ({ id, type: 'prompt', position: { x, y }, data: {}, measured: { width: 100, height: 50 }, ...extra }) as unknown as CanvasNode;
const conn = (source: string, target: string): CanvasConnection =>
  ({ id: `${source}-${target}`, source, target, sourceHandle: null, targetHandle: null }) as CanvasConnection;

describe('canArrangeSelection', () => {
  it('true only when 2+ top-level nodes are selected', () => {
    const nodes = [n('a', 0, 0), n('b', 0, 300), n('c', 0, 600, { parentId: 'g1' })];
    expect(canArrangeSelection(nodes, [])).toBe(false);
    expect(canArrangeSelection(nodes, ['a'])).toBe(false);
    expect(canArrangeSelection(nodes, ['a', 'b'])).toBe(true);
    // a child + a top-level doesn't count (only 1 top-level in scope)
    expect(canArrangeSelection(nodes, ['a', 'c'])).toBe(false);
  });
});

describe('arrangeSelected', () => {
  it('lays out only the selected subset; unselected stay put', () => {
    const nodes = [n('a', 0, 0), n('b', 0, 300), n('c', 900, 900)];
    const next = arrangeSelected(nodes, [conn('a', 'b')], ['a', 'b'])!;
    expect(next).not.toBeNull();
    const c = next.find((x) => (x as { id: string }).id === 'c') as { position: { x: number; y: number } };
    expect(c.position).toEqual({ x: 900, y: 900 });
    const a = next.find((x) => (x as { id: string }).id === 'a') as { position: { x: number; y: number } };
    const b = next.find((x) => (x as { id: string }).id === 'b') as { position: { x: number; y: number } };
    expect(b.position.x).toBeGreaterThan(a.position.x); // a→b left-to-right
  });

  it('no selection lays out the whole graph', () => {
    const nodes = [n('a', 0, 0), n('b', 0, 300)];
    expect(arrangeSelected(nodes, [conn('a', 'b')], [])).not.toBeNull();
  });

  it('returns null when fewer than 2 in scope', () => {
    // single node, no selection → whole-graph scope of 1 → null
    expect(arrangeSelected([n('a', 0, 0)], [], [])).toBeNull();
    // 2+ selected but only 1 is top-level → selected-subset scope of 1 → null
    const nodes = [n('a', 0, 0), n('kid', 5, 5, { parentId: 'a' })];
    expect(arrangeSelected(nodes, [], ['a', 'kid'])).toBeNull();
  });

  it('never moves group children (parent-relative positions preserved)', () => {
    const nodes = [n('a', 0, 0), n('b', 0, 300), n('kid', 5, 5, { parentId: 'a' })];
    const next = arrangeSelected(nodes, [], ['a', 'b'])!;
    const kid = next.find((x) => (x as { id: string }).id === 'kid') as { position: { x: number; y: number } };
    expect(kid.position).toEqual({ x: 5, y: 5 });
  });
});
