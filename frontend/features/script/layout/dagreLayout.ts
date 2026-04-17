import { graphlib, layout } from '@dagrejs/dagre';
import type { ScriptNode, ScriptEdge } from '../../../stores/scriptCanvasStore';

const NODE_WIDTH = 520;
const ESTIMATED_NODE_HEIGHT = 300;
const NODE_GAP = 80;

/**
 * Apply dagre top-to-bottom layout to the main chapter nodes (non-branch).
 * Branch nodes retain their existing positions and are returned unchanged.
 */
export function applyDagreLayout(
  nodes: ScriptNode[],
  edges: ScriptEdge[],
): ScriptNode[] {
  const g = new graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: NODE_GAP, ranksep: NODE_GAP });

  // Only layout main chapter nodes (type === 'chapterNode', no branchType)
  const mainNodes = nodes.filter(
    (n) => n.type === 'chapterNode' && !n.data.branchType,
  );
  const otherNodes = nodes.filter(
    (n) => n.type !== 'chapterNode' || n.data.branchType,
  );

  for (const node of mainNodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: ESTIMATED_NODE_HEIGHT });
  }

  // Include only edges whose both endpoints are main nodes
  const mainIds = new Set(mainNodes.map((n) => n.id));
  for (const edge of edges) {
    if (mainIds.has(edge.source) && mainIds.has(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  }

  layout(g);

  const layoutedMain = mainNodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - ESTIMATED_NODE_HEIGHT / 2,
      },
    };
  });

  return [...layoutedMain, ...otherNodes];
}
