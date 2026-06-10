/**
 * Pure stroke-model helpers for the mask/brush tool (Phase 3 Day 11).
 *
 * A mask is a list of strokes. Each stroke is a polyline of
 * normalized [0, 1] points painted with either the brush (keep) or
 * the eraser (un-keep). The model is deliberately resolution-free:
 * the SVG overlay renders it at display size, the export step
 * rasterizes it at the source image's natural size.
 *
 * All helpers are immutable.
 */

export type MaskTool = 'brush' | 'eraser';

export interface MaskPoint {
  /** [0, 1] relative to the image's rendered box. */
  x: number;
  y: number;
}

export interface MaskStroke {
  tool: MaskTool;
  /** Stroke width as a fraction of the image width, e.g. 0.05. */
  size: number;
  points: MaskPoint[];
}

export interface BrushSizePreset {
  label: string;
  value: number;
}

export const BRUSH_SIZES: ReadonlyArray<BrushSizePreset> = [
  { label: 'S', value: 0.02 },
  { label: 'M', value: 0.05 },
  { label: 'L', value: 0.1 },
];

/** Points closer than this to the stroke's last point are dropped —
 *  keeps high-frequency pointermove streams from bloating the model. */
const MIN_POINT_DISTANCE = 0.003;

function clamp01(v: number): number {
  if (Number.isNaN(v)) return 0;
  return Math.min(1, Math.max(0, v));
}

export function clampPoint(point: MaskPoint): MaskPoint {
  return { x: clamp01(point.x), y: clamp01(point.y) };
}

/** Start a new stroke at `point`. */
export function beginStroke(
  strokes: MaskStroke[],
  tool: MaskTool,
  size: number,
  point: MaskPoint,
): MaskStroke[] {
  return [...strokes, { tool, size, points: [clampPoint(point)] }];
}

/** Append `point` to the last stroke; no-op when there is no stroke
 *  yet or the point is within MIN_POINT_DISTANCE of the previous one. */
export function extendStroke(
  strokes: MaskStroke[],
  point: MaskPoint,
): MaskStroke[] {
  if (strokes.length === 0) return strokes;
  const last = strokes[strokes.length - 1];
  const prev = last.points[last.points.length - 1];
  const next = clampPoint(point);
  if (
    prev &&
    Math.hypot(next.x - prev.x, next.y - prev.y) < MIN_POINT_DISTANCE
  ) {
    return strokes;
  }
  const updated: MaskStroke = { ...last, points: [...last.points, next] };
  return [...strokes.slice(0, -1), updated];
}

/** True when at least one brush stroke exists — an eraser-only mask
 *  keeps nothing and the cutout endpoint would reject it. */
export function hasMaskContent(strokes: MaskStroke[]): boolean {
  return strokes.some(
    (stroke) => stroke.tool === 'brush' && stroke.points.length > 0,
  );
}

/**
 * Build an SVG path string for a stroke's points, scaled by
 * (scaleX, scaleY). Single-point strokes get a zero-length segment so
 * round line caps render them as dots.
 */
export function strokePath(
  points: MaskPoint[],
  scaleX: number,
  scaleY: number,
): string {
  if (points.length === 0) return '';
  const fmt = (p: MaskPoint) => `${p.x * scaleX} ${p.y * scaleY}`;
  if (points.length === 1) {
    return `M ${fmt(points[0])} L ${fmt(points[0])}`;
  }
  return `M ${fmt(points[0])} ${points
    .slice(1)
    .map((p) => `L ${fmt(p)}`)
    .join(' ')}`;
}
