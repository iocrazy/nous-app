// canvas-kit/arrangeLayout.ts
//
// Topology auto-arrange (Infinite-Canvas parity G6, Infinite's
// arrangeSelectedCanvasNodes): a dagre left-to-right layered layout over
// the current graph, honoring measured node sizes. Pure — returns NEW
// node objects with updated positions; the caller commits through its
// store (a user-initiated edit, so it SHOULD be undoable).

import dagre from '@dagrejs/dagre';

interface ArrangeNode {
  id: string;
  position: { x: number; y: number };
  measured?: { width?: number; height?: number };
  [k: string]: unknown;
}

interface ArrangeEdge {
  source: string;
  target: string;
  [k: string]: unknown;
}

const FALLBACK_WIDTH = 240;
const FALLBACK_HEIGHT = 120;
const RANK_GAP = 80;
const NODE_GAP = 40;

export function arrangeLayout<N extends ArrangeNode>(
  nodes: N[],
  edges: ArrangeEdge[],
): N[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', ranksep: RANK_GAP, nodesep: NODE_GAP });
  g.setDefaultEdgeLabel(() => ({}));

  const ids = new Set(nodes.map((n) => n.id));
  for (const node of nodes) {
    g.setNode(node.id, {
      width: node.measured?.width ?? FALLBACK_WIDTH,
      height: node.measured?.height ?? FALLBACK_HEIGHT,
    });
  }
  for (const edge of edges) {
    if (ids.has(edge.source) && ids.has(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  }

  dagre.layout(g);

  const laidOut = nodes.map((node) => {
    const placed = g.node(node.id);
    if (!placed || !Number.isFinite(placed.x) || !Number.isFinite(placed.y)) {
      return { ...node };
    }
    const width = node.measured?.width ?? FALLBACK_WIDTH;
    const height = node.measured?.height ?? FALLBACK_HEIGHT;
    // dagre positions are centers; React Flow positions are top-left.
    return {
      ...node,
      position: { x: placed.x - width / 2, y: placed.y - height / 2 },
    };
  });

  // dagre lays out from the origin; translate the result back onto the
  // graph's original centroid so Arrange never teleports a graph built at
  // (3000, 2000) out of the viewport.
  if (laidOut.length === 0) return laidOut;
  const centroid = (list: { position: { x: number; y: number } }[]) => ({
    x: list.reduce((s, n) => s + n.position.x, 0) / list.length,
    y: list.reduce((s, n) => s + n.position.y, 0) / list.length,
  });
  const before = centroid(nodes);
  const after = centroid(laidOut);
  const dx = before.x - after.x;
  const dy = before.y - after.y;
  return laidOut.map((node) => ({
    ...node,
    position: { x: node.position.x + dx, y: node.position.y + dy },
  }));
}
