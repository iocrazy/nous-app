// Konva annotation shape types, helpers, and serialization utilities.

// ─── Types ──────────────────────────────────────────────────────────────────

export type AnnotationToolType = 'select' | 'pen' | 'line' | 'rect' | 'ellipse' | 'arrow' | 'text' | 'eraser';

export interface BaseAnnotation {
  id: string;
  color: string;
  strokeWidth: number;
  opacity: number;
}

export interface PenAnnotation extends BaseAnnotation {
  type: 'pen';
  points: number[];
}

export interface LineAnnotation extends BaseAnnotation {
  type: 'line';
  points: [number, number, number, number];
}

export interface RectAnnotation extends BaseAnnotation {
  type: 'rect';
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface EllipseAnnotation extends BaseAnnotation {
  type: 'ellipse';
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ArrowAnnotation extends BaseAnnotation {
  type: 'arrow';
  points: [number, number, number, number];
}

export interface TextAnnotation extends BaseAnnotation {
  type: 'text';
  x: number;
  y: number;
  text: string;
  fontSize: number;
}

export type AnnotationItem =
  | PenAnnotation
  | LineAnnotation
  | RectAnnotation
  | EllipseAnnotation
  | ArrowAnnotation
  | TextAnnotation;

// ─── Constants ──────────────────────────────────────────────────────────────

export const PRESET_COLORS = [
  '#FF4444', '#FF8800', '#FFDD00', '#44CC44',
  '#4488FF', '#FFFFFF', '#000000',
] as const;

export const STROKE_WIDTH_OPTIONS = [1, 2, 4, 6, 8, 12] as const;

export const FONT_SIZE_OPTIONS = [12, 16, 20, 24, 32, 48] as const;

export const MAX_UNDO_STACK = 40;

// ─── ID generation ──────────────────────────────────────────────────────────

export function createAnnotationId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// ─── Geometry helpers ───────────────────────────────────────────────────────

export function normalizeRect(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): { x: number; y: number; width: number; height: number } {
  return {
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    width: Math.abs(x2 - x1),
    height: Math.abs(y2 - y1),
  };
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

// ─── Draft → Annotation builder ─────────────────────────────────────────────

export interface DraftState {
  tool: Exclude<AnnotationToolType, 'select' | 'eraser' | 'text'>;
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
  points?: number[];
}

export function buildAnnotationFromDraft(
  draft: DraftState,
  color: string,
  strokeWidth: number,
  opacity: number,
): AnnotationItem | null {
  const { tool, startX, startY, currentX, currentY, points } = draft;

  if (tool === 'pen') {
    const pts = points ?? [startX, startY];
    if (pts.length < 4) return null;
    return {
      id: createAnnotationId(),
      type: 'pen',
      points: [...pts],
      color,
      strokeWidth,
      opacity,
    };
  }

  if (tool === 'line') {
    if (Math.hypot(currentX - startX, currentY - startY) < 4) return null;
    return {
      id: createAnnotationId(),
      type: 'line',
      points: [startX, startY, currentX, currentY],
      color,
      strokeWidth,
      opacity,
    };
  }

  if (tool === 'arrow') {
    if (Math.hypot(currentX - startX, currentY - startY) < 4) return null;
    return {
      id: createAnnotationId(),
      type: 'arrow',
      points: [startX, startY, currentX, currentY],
      color,
      strokeWidth,
      opacity,
    };
  }

  const rect = normalizeRect(startX, startY, currentX, currentY);
  if (rect.width < 4 || rect.height < 4) return null;

  if (tool === 'rect') {
    return { id: createAnnotationId(), type: 'rect', ...rect, color, strokeWidth, opacity };
  }

  // ellipse
  return { id: createAnnotationId(), type: 'ellipse', ...rect, color, strokeWidth, opacity };
}

// ─── Position / transform updates (immutable) ──────────────────────────────

function getPointsBounds(points: number[]): { minX: number; minY: number } {
  const xs = points.filter((_, i) => i % 2 === 0);
  const ys = points.filter((_, i) => i % 2 === 1);
  return { minX: Math.min(...xs), minY: Math.min(...ys) };
}

export function moveAnnotation(
  item: AnnotationItem,
  newX: number,
  newY: number,
): AnnotationItem {
  if (item.type === 'pen') {
    const { minX, minY } = getPointsBounds(item.points);
    return {
      ...item,
      points: item.points.map((p, i) => (i % 2 === 0 ? p + (newX - minX) : p + (newY - minY))),
    };
  }

  if (item.type === 'line' || item.type === 'arrow') {
    const { minX, minY } = getPointsBounds(item.points);
    const dx = newX - minX;
    const dy = newY - minY;
    return {
      ...item,
      points: [
        item.points[0] + dx,
        item.points[1] + dy,
        item.points[2] + dx,
        item.points[3] + dy,
      ] as [number, number, number, number],
    };
  }

  return { ...item, x: newX, y: newY };
}

export function transformAnnotation(
  item: AnnotationItem,
  newX: number,
  newY: number,
  scaleX: number,
  scaleY: number,
): AnnotationItem {
  if (item.type === 'rect' || item.type === 'ellipse') {
    return {
      ...item,
      x: newX,
      y: newY,
      width: Math.max(5, item.width * scaleX),
      height: Math.max(5, item.height * scaleY),
    };
  }

  if (item.type === 'text') {
    return {
      ...item,
      x: newX,
      y: newY,
      fontSize: Math.max(8, Math.round(item.fontSize * Math.max(scaleX, scaleY))),
    };
  }

  if (item.type === 'pen' || item.type === 'line' || item.type === 'arrow') {
    const { minX, minY } = getPointsBounds(item.points);
    const newPoints = item.points.map((p, i) =>
      i % 2 === 0 ? newX + (p - minX) * scaleX : newY + (p - minY) * scaleY,
    );
    if (item.type === 'pen') {
      return { ...item, points: newPoints };
    }
    return {
      ...item,
      points: [newPoints[0], newPoints[1], newPoints[2], newPoints[3]] as [number, number, number, number],
    };
  }

  return item;
}

// ─── Export: flatten annotations onto image canvas ──────────────────────────

export function flattenAnnotationsToCanvas(
  image: HTMLImageElement,
  annotations: readonly AnnotationItem[],
): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Failed to create canvas context');

  ctx.drawImage(image, 0, 0);

  for (const item of annotations) {
    ctx.save();
    ctx.globalAlpha = item.opacity;
    ctx.strokeStyle = item.color;
    ctx.fillStyle = item.color;
    ctx.lineWidth = item.strokeWidth;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    if (item.type === 'pen' && item.points.length >= 4) {
      ctx.beginPath();
      ctx.moveTo(item.points[0], item.points[1]);
      for (let i = 2; i < item.points.length; i += 2) {
        ctx.lineTo(item.points[i], item.points[i + 1]);
      }
      ctx.stroke();
    } else if (item.type === 'line') {
      ctx.beginPath();
      ctx.moveTo(item.points[0], item.points[1]);
      ctx.lineTo(item.points[2], item.points[3]);
      ctx.stroke();
    } else if (item.type === 'rect') {
      ctx.strokeRect(item.x, item.y, item.width, item.height);
    } else if (item.type === 'ellipse') {
      const cx = item.x + item.width / 2;
      const cy = item.y + item.height / 2;
      ctx.beginPath();
      ctx.ellipse(cx, cy, Math.abs(item.width / 2), Math.abs(item.height / 2), 0, 0, Math.PI * 2);
      ctx.stroke();
    } else if (item.type === 'arrow') {
      const [x1, y1, x2, y2] = item.points;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
      const angle = Math.atan2(y2 - y1, x2 - x1);
      const headLen = Math.max(10, item.strokeWidth * 3);
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6));
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6));
      ctx.stroke();
    } else if (item.type === 'text') {
      ctx.font = `bold ${item.fontSize}px sans-serif`;
      ctx.fillText(item.text, item.x, item.y + item.fontSize);
    }

    ctx.restore();
  }

  return canvas;
}
