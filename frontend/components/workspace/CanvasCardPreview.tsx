/**
 * CanvasCardPreview — the top half of a canvas card: a dot-grid field with
 * the canvas's REAL node layout rendered as a minimap (rounded rects +
 * connection lines). The card reads as a window into the canvas, using the
 * same visual language as the editor surface.
 *
 * nodes_json is an opaque JSONB pass-through (React Flow owns the shape),
 * so every field is parsed defensively — a canvas summary without geometry
 * simply falls back to the empty-glyph state.
 */

import type { CSSProperties } from 'react';
import { Frame } from 'lucide-react';
import type {
  CanvasConnection,
  CanvasNode,
} from '../../features/canvas-core/types';

const MAX_PREVIEW_NODES = 60;
const DEFAULT_NODE_WIDTH = 180;
const DEFAULT_NODE_HEIGHT = 100;
const BOUNDS_PADDING = 60;
const NODE_CORNER_RADIUS = 10;

const DOT_GRID_STYLE: CSSProperties = {
  backgroundImage:
    'radial-gradient(color-mix(in srgb, var(--ink-500) 40%, transparent) 1px, transparent 1px)',
  backgroundSize: '14px 14px',
};

interface MiniNode {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface MiniEdge {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

function toFinite(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : null;
}

function extractMiniNodes(nodes: CanvasNode[]): MiniNode[] {
  const out: MiniNode[] = [];
  for (const node of nodes.slice(0, MAX_PREVIEW_NODES)) {
    const position = asRecord(node.position);
    const x = toFinite(position?.x);
    const y = toFinite(position?.y);
    if (x === null || y === null) continue;
    const measured = asRecord(node.measured);
    const w = toFinite(node.width) ?? toFinite(measured?.width) ?? DEFAULT_NODE_WIDTH;
    const h = toFinite(node.height) ?? toFinite(measured?.height) ?? DEFAULT_NODE_HEIGHT;
    out.push({ id: String(node.id ?? out.length), x, y, w, h });
  }
  return out;
}

function extractMiniEdges(
  connections: CanvasConnection[],
  nodes: MiniNode[],
): MiniEdge[] {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const out: MiniEdge[] = [];
  for (const conn of connections) {
    const source = byId.get(String(conn.source ?? ''));
    const target = byId.get(String(conn.target ?? ''));
    if (!source || !target) continue;
    out.push({
      x1: source.x + source.w / 2,
      y1: source.y + source.h / 2,
      x2: target.x + target.w / 2,
      y2: target.y + target.h / 2,
    });
  }
  return out;
}

interface CanvasCardPreviewProps {
  nodes: CanvasNode[] | undefined;
  connections: CanvasConnection[] | undefined;
}

export function CanvasCardPreview({ nodes, connections }: CanvasCardPreviewProps) {
  const miniNodes = extractMiniNodes(Array.isArray(nodes) ? nodes : []);
  const miniEdges = extractMiniEdges(
    Array.isArray(connections) ? connections : [],
    miniNodes,
  );

  if (miniNodes.length === 0) {
    return (
      <div
        className="flex h-28 w-full items-center justify-center border-b border-ink-800 bg-ink-950/40"
        style={DOT_GRID_STYLE}
      >
        <Frame
          size={22}
          className="text-ink-700 transition-colors group-hover:text-indigo-400/50"
        />
      </div>
    );
  }

  const minX = Math.min(...miniNodes.map((n) => n.x)) - BOUNDS_PADDING;
  const minY = Math.min(...miniNodes.map((n) => n.y)) - BOUNDS_PADDING;
  const maxX = Math.max(...miniNodes.map((n) => n.x + n.w)) + BOUNDS_PADDING;
  const maxY = Math.max(...miniNodes.map((n) => n.y + n.h)) + BOUNDS_PADDING;

  return (
    <div
      className="h-28 w-full border-b border-ink-800 bg-ink-950/40"
      style={DOT_GRID_STYLE}
    >
      <svg
        aria-hidden
        className="h-full w-full"
        viewBox={`${minX} ${minY} ${maxX - minX} ${maxY - minY}`}
        preserveAspectRatio="xMidYMid meet"
      >
        {miniEdges.map((edge, i) => (
          <line
            key={i}
            x1={edge.x1}
            y1={edge.y1}
            x2={edge.x2}
            y2={edge.y2}
            vectorEffect="non-scaling-stroke"
            className="stroke-ink-600/60 transition-colors group-hover:stroke-indigo-400/40"
            strokeWidth={1}
          />
        ))}
        {miniNodes.map((node) => (
          <rect
            key={node.id}
            x={node.x}
            y={node.y}
            width={node.w}
            height={node.h}
            rx={NODE_CORNER_RADIUS}
            vectorEffect="non-scaling-stroke"
            className="fill-ink-800 stroke-ink-600 transition-colors group-hover:stroke-indigo-400/70"
            strokeWidth={1}
          />
        ))}
      </svg>
    </div>
  );
}

export default CanvasCardPreview;
