// canvas-kit/KnifeOverlay.tsx
//
// Knife mode (Infinite-Canvas parity — canvas.js knife trail): an overlay
// that captures a drag, draws the red dashed trail, and cuts every edge
// whose sampled polyline a trail segment crosses — LIVE during the drag,
// exactly like Infinite. Edges attached to a node the segment passes
// through are spared (you can slice close past a node without shredding
// its wiring). Sampling is injected: the real adapter reads the rendered
// React Flow DOM; tests hand in plain polylines.

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  segmentHitsPolyline,
  segmentIntersectsRect,
  type Point,
  type Rect,
} from './knifeGeometry';

export interface EdgeSample {
  id: string;
  /** Screen-space polyline approximating the edge path. */
  points: Point[];
}

export interface NodeSample {
  id: string;
  /** Screen-space bounding rect. */
  rect: Rect;
}

export interface KnifeOverlayProps {
  /** Sample the rendered edges (screen space). Called once per drag start —
   *  the graph doesn't move mid-cut. */
  sampleEdges: () => EdgeSample[];
  sampleNodes: () => NodeSample[];
  /** Edge id → its endpoint node ids (for the node pass-through waiver). */
  edgeEndpoints: () => Record<string, { source: string; target: string }>;
  /** Called with newly-cut edge ids (already-cut ids never repeat). */
  onCut: (edgeIds: string[]) => void;
  /** Escape pressed — leave knife mode. */
  onExit: () => void;
}

const CUT_THRESHOLD_PX = 10;

export function KnifeOverlay({
  sampleEdges,
  sampleNodes,
  edgeEndpoints,
  onCut,
  onExit,
}: KnifeOverlayProps) {
  const [trail, setTrail] = useState<Point[]>([]);
  const draggingRef = useRef(false);
  const edgesRef = useRef<EdgeSample[]>([]);
  const nodesRef = useRef<NodeSample[]>([]);
  const endpointsRef = useRef<Record<string, { source: string; target: string }>>({});
  const cutRef = useRef<Set<string>>(new Set());
  const lastRef = useRef<Point | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    rootRef.current?.focus();
  }, []);

  const toLocal = useCallback((e: React.MouseEvent): Point => {
    const rect = rootRef.current?.getBoundingClientRect();
    return { x: e.clientX - (rect?.left ?? 0), y: e.clientY - (rect?.top ?? 0) };
  }, []);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      draggingRef.current = true;
      edgesRef.current = sampleEdges();
      nodesRef.current = sampleNodes();
      endpointsRef.current = edgeEndpoints();
      cutRef.current = new Set();
      const p = toLocal(e);
      lastRef.current = p;
      setTrail([p]);
    },
    [sampleEdges, sampleNodes, edgeEndpoints, toLocal],
  );

  const onMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!draggingRef.current || !lastRef.current) return;
      const p = toLocal(e);
      const a = lastRef.current;
      lastRef.current = p;
      setTrail((t) => (t.length > 120 ? [...t.slice(-120), p] : [...t, p]));

      // Node waiver: edges hanging off a node this segment passes through
      // are spared (Infinite's nodeHits set).
      const sparedNodes = new Set(
        nodesRef.current
          .filter((n) => segmentIntersectsRect(a, p, n.rect))
          .map((n) => n.id),
      );
      const newlyCut: string[] = [];
      for (const edge of edgesRef.current) {
        if (cutRef.current.has(edge.id)) continue;
        const ends = endpointsRef.current[edge.id];
        if (ends && (sparedNodes.has(ends.source) || sparedNodes.has(ends.target))) {
          continue;
        }
        if (segmentHitsPolyline(a, p, edge.points, CUT_THRESHOLD_PX)) {
          cutRef.current.add(edge.id);
          newlyCut.push(edge.id);
        }
      }
      if (newlyCut.length > 0) onCut(newlyCut);
    },
    [onCut, toLocal],
  );

  const endDrag = useCallback(() => {
    draggingRef.current = false;
    lastRef.current = null;
    setTrail([]);
  }, []);

  return (
    <div
      ref={rootRef}
      data-testid="knife-overlay"
      tabIndex={-1}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={endDrag}
      onMouseLeave={endDrag}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onExit();
      }}
      className="absolute inset-0 z-40 cursor-crosshair outline-none"
    >
      {trail.length > 1 && (
        <svg className="pointer-events-none absolute inset-0 h-full w-full">
          <polyline
            data-testid="knife-trail"
            points={trail.map((p) => `${p.x},${p.y}`).join(' ')}
            className="mh-knife-trail"
            fill="none"
          />
        </svg>
      )}
    </div>
  );
}
