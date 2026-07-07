/**
 * computeAlignmentGuides — pure alignment-line math (Phase B Task 2).
 *
 * Exhaustive coverage: one hit per edge (left/centerX/right, top/centerY/bottom),
 * tolerance boundary (5 hits, 6 misses), nearest-of-multiple candidates, the
 * empty (no-hit) result, and the exact snapped coordinate a hit reports.
 *
 * The reference rect anchors edges at nicely separated coordinates:
 *   other = { x:0, y:0, width:100, height:100 }
 *     x edges → left 0, centerX 50, right 100
 *     y edges → top 0, centerY 50, bottom 100
 * The dragged rect is 20×20 (center offset 10, far offset 20), and the untested
 * axis is parked far away so only the intended edge is ever within tolerance.
 */
import { describe, it, expect } from 'vitest';
import { computeAlignmentGuides, type Rect } from '../../canvas-kit/alignmentGuides';

const other: Rect = { x: 0, y: 0, width: 100, height: 100 };
const DRAG = 20; // dragged rect side length
/** Dragged rect at (x,y); parks the untested axis far from any edge. */
const at = (x: number, y: number): Rect => ({ x, y, width: DRAG, height: DRAG });
const FAR = 500;

describe('computeAlignmentGuides', () => {
  describe('vertical (x-axis) single-edge hits', () => {
    it("aligns the dragged rect's left edge to another left edge", () => {
      const g = computeAlignmentGuides(at(0, FAR), [other]);
      expect(g.vertical).toBe(0);
      expect(g.snappedX).toBe(0);
      expect(g.horizontal).toBeUndefined();
    });

    it("aligns the dragged rect's center to another center", () => {
      const g = computeAlignmentGuides(at(40, FAR), [other]);
      expect(g.vertical).toBe(50);
      expect(g.snappedX).toBe(40);
    });

    it("aligns the dragged rect's right edge to another right edge", () => {
      const g = computeAlignmentGuides(at(80, FAR), [other]);
      expect(g.vertical).toBe(100);
      expect(g.snappedX).toBe(80);
    });
  });

  describe('horizontal (y-axis) single-edge hits', () => {
    it("aligns the dragged rect's top edge to another top edge", () => {
      const g = computeAlignmentGuides(at(FAR, 0), [other]);
      expect(g.horizontal).toBe(0);
      expect(g.snappedY).toBe(0);
      expect(g.vertical).toBeUndefined();
    });

    it("aligns the dragged rect's center to another center", () => {
      const g = computeAlignmentGuides(at(FAR, 40), [other]);
      expect(g.horizontal).toBe(50);
      expect(g.snappedY).toBe(40);
    });

    it("aligns the dragged rect's bottom edge to another bottom edge", () => {
      const g = computeAlignmentGuides(at(FAR, 80), [other]);
      expect(g.horizontal).toBe(100);
      expect(g.snappedY).toBe(80);
    });
  });

  describe('tolerance boundary', () => {
    it('snaps at exactly 5px of slack', () => {
      const g = computeAlignmentGuides(at(5, FAR), [other]);
      expect(g.vertical).toBe(0);
      expect(g.snappedX).toBe(0);
    });

    it('does not snap at 6px of slack', () => {
      const g = computeAlignmentGuides(at(6, FAR), [other]);
      expect(g.vertical).toBeUndefined();
      expect(g.snappedX).toBeUndefined();
    });
  });

  it('picks the nearest edge when several are within tolerance', () => {
    // Two candidates for the dragged left edge (at x=1): other-left 0 (Δ1) and
    // other-left 3 (Δ2). Nearest wins → guide at 0.
    const a: Rect = { x: 0, y: 0, width: DRAG, height: DRAG };
    const b: Rect = { x: 3, y: 0, width: DRAG, height: DRAG };
    const g = computeAlignmentGuides(at(1, FAR), [a, b]);
    expect(g.vertical).toBe(0);
    expect(g.snappedX).toBe(0);
  });

  it('returns an empty result when nothing is within tolerance', () => {
    const g = computeAlignmentGuides(at(FAR, FAR), [other]);
    expect(g).toEqual({});
  });

  it('reports snapX that lands the matching edge on the guide', () => {
    // Dragged center (offset 10) meets other-left 0: guide at 0, so the rect's
    // x must move to -10 to seat its center on the line.
    const leftOnly: Rect = { x: 0, y: 0, width: 200, height: 200 };
    const g = computeAlignmentGuides({ x: -10, y: FAR, width: 20, height: 20 }, [leftOnly]);
    expect(g.vertical).toBe(0);
    expect(g.snappedX).toBe(-10);
  });
});
