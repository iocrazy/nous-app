import { describe, expect, it } from 'vitest';

import {
  beginStroke,
  clampPoint,
  extendStroke,
  hasMaskContent,
  strokePath,
  type MaskStroke,
} from './maskMath';

describe('beginStroke / extendStroke', () => {
  it('beginStroke appends a one-point stroke without mutating input', () => {
    const initial: MaskStroke[] = [];
    const next = beginStroke(initial, 'brush', 0.05, { x: 0.5, y: 0.5 });
    expect(initial).toEqual([]);
    expect(next).toHaveLength(1);
    expect(next[0]).toEqual({
      tool: 'brush',
      size: 0.05,
      points: [{ x: 0.5, y: 0.5 }],
    });
  });

  it('extendStroke appends to the last stroke immutably', () => {
    const one = beginStroke([], 'brush', 0.05, { x: 0.1, y: 0.1 });
    const two = extendStroke(one, { x: 0.2, y: 0.2 });
    expect(one[0].points).toHaveLength(1);
    expect(two[0].points).toHaveLength(2);
    expect(two[0].points[1]).toEqual({ x: 0.2, y: 0.2 });
  });

  it('extendStroke drops points closer than the distance floor', () => {
    const one = beginStroke([], 'brush', 0.05, { x: 0.1, y: 0.1 });
    const same = extendStroke(one, { x: 0.1001, y: 0.1001 });
    expect(same).toBe(one);
  });

  it('extendStroke with no strokes is a no-op', () => {
    expect(extendStroke([], { x: 0.5, y: 0.5 })).toEqual([]);
  });

  it('points are clamped into [0, 1]', () => {
    const next = beginStroke([], 'eraser', 0.02, { x: -0.5, y: 1.7 });
    expect(next[0].points[0]).toEqual({ x: 0, y: 1 });
    expect(clampPoint({ x: NaN, y: 0.5 })).toEqual({ x: 0, y: 0.5 });
  });
});

describe('hasMaskContent', () => {
  it('false when empty or eraser-only', () => {
    expect(hasMaskContent([])).toBe(false);
    const eraserOnly = beginStroke([], 'eraser', 0.05, { x: 0.5, y: 0.5 });
    expect(hasMaskContent(eraserOnly)).toBe(false);
  });

  it('true once a brush stroke exists', () => {
    const strokes = beginStroke([], 'brush', 0.05, { x: 0.5, y: 0.5 });
    expect(hasMaskContent(strokes)).toBe(true);
  });
});

describe('strokePath', () => {
  it('builds a scaled M/L path', () => {
    const path = strokePath(
      [
        { x: 0.1, y: 0.2 },
        { x: 0.3, y: 0.4 },
      ],
      1000,
      500,
    );
    expect(path).toBe('M 100 100 L 300 200');
  });

  it('single point becomes a zero-length segment (dot via round cap)', () => {
    expect(strokePath([{ x: 0.5, y: 0.5 }], 100, 100)).toBe(
      'M 50 50 L 50 50',
    );
  });

  it('empty points yields empty string', () => {
    expect(strokePath([], 100, 100)).toBe('');
  });
});
