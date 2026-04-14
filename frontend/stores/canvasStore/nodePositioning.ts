/**
 * Node position computation — finds an optimal non-overlapping position
 * for a newly added node relative to a source node, respecting viewport bounds.
 */
import { DEFAULT_NODE_WIDTH } from '../../features/storyboard/domain/canvasNodes';
import type { CanvasNode } from '../../features/storyboard/domain/canvasNodes';

export interface FindNodePositionContext {
  nodes: CanvasNode[];
  currentViewport: { x: number; y: number; zoom: number };
  canvasViewportSize: { width: number; height: number };
}

export function computeNodePosition(
  ctx: FindNodePositionContext,
  sourceNodeId: string,
  newNodeWidth: number,
  newNodeHeight: number
): { x: number; y: number } {
  const { nodes, currentViewport, canvasViewportSize } = ctx;
  const sourceNode = nodes.find((n) => n.id === sourceNodeId);
  if (!sourceNode) {
    return { x: 100, y: 100 };
  }

  const collides = (x: number, y: number, width: number, height: number) => {
    return nodes.some((node) => {
      const nodeWidth = node.measured?.width ?? DEFAULT_NODE_WIDTH;
      const nodeHeight = node.measured?.height ?? 200;
      const margin = 8;
      return (
        x < node.position.x + nodeWidth + margin &&
        x + width + margin > node.position.x &&
        y < node.position.y + nodeHeight + margin &&
        y + height + margin > node.position.y
      );
    });
  };

  const sourceWidth = sourceNode.measured?.width ?? DEFAULT_NODE_WIDTH;
  const sourceHeight = sourceNode.measured?.height ?? 200;
  const anchorX = sourceNode.position.x + sourceWidth + 28;
  const anchorY = sourceNode.position.y;

  const zoom = Math.max(0.01, currentViewport.zoom || 1);
  const viewportWidth = canvasViewportSize.width;
  const viewportHeight = canvasViewportSize.height;
  const hasViewportBounds = viewportWidth > 0 && viewportHeight > 0;
  const visibleBounds = hasViewportBounds
    ? {
        minX: -currentViewport.x / zoom,
        minY: -currentViewport.y / zoom,
        maxX: -currentViewport.x / zoom + viewportWidth / zoom,
        maxY: -currentViewport.y / zoom + viewportHeight / zoom,
      }
    : null;

  const overflowAmount = (x: number, y: number): number => {
    if (!visibleBounds) {
      return 0;
    }
    const overLeft = Math.max(0, visibleBounds.minX - x);
    const overTop = Math.max(0, visibleBounds.minY - y);
    const overRight = Math.max(0, x + newNodeWidth - visibleBounds.maxX);
    const overBottom = Math.max(0, y + newNodeHeight - visibleBounds.maxY);
    return overLeft + overTop + overRight + overBottom;
  };

  const stepX = Math.max(newNodeWidth + 12, 110);
  const stepY = Math.max(Math.round(newNodeHeight * 0.35), 54);
  const baseCandidates = [
    { x: anchorX, y: anchorY },
    { x: sourceNode.position.x, y: sourceNode.position.y + sourceHeight + 20 },
    { x: sourceNode.position.x - newNodeWidth - 20, y: sourceNode.position.y },
    { x: sourceNode.position.x, y: sourceNode.position.y - newNodeHeight - 20 },
  ];

  let bestInView: { x: number; y: number; score: number } | null = null;
  let bestOutOfView: { x: number; y: number; score: number } | null = null;

  const evaluateCandidate = (x: number, y: number) => {
    if (collides(x, y, newNodeWidth, newNodeHeight)) {
      return;
    }

    const dx = x - anchorX;
    const dy = y - anchorY;
    const distanceScore = Math.hypot(dx, dy);
    const upwardPenalty = dy < 0 ? Math.abs(dy) * 0.25 : 0;
    const overflow = overflowAmount(x, y);
    const score = distanceScore + upwardPenalty + overflow * 1000;
    const candidate = { x, y, score };

    if (overflow === 0) {
      if (!bestInView || score < bestInView.score) {
        bestInView = candidate;
      }
    } else if (!bestOutOfView || score < bestOutOfView.score) {
      bestOutOfView = candidate;
    }
  };

  for (const base of baseCandidates) {
    evaluateCandidate(base.x, base.y);
  }

  for (let ring = 1; ring <= 8; ring += 1) {
    const offsets = [
      { x: ring, y: 0 },
      { x: ring, y: 1 },
      { x: ring, y: -1 },
      { x: 0, y: ring },
      { x: 0, y: -ring },
      { x: -ring, y: 0 },
      { x: ring, y: 2 },
      { x: ring, y: -2 },
      { x: -ring, y: 1 },
      { x: -ring, y: -1 },
    ];
    for (const offset of offsets) {
      evaluateCandidate(anchorX + offset.x * stepX, anchorY + offset.y * stepY);
    }
  }

  if (!bestInView && visibleBounds) {
    const padding = 8;
    const minX = visibleBounds.minX + padding;
    const maxX = visibleBounds.maxX - newNodeWidth - padding;
    const minY = visibleBounds.minY + padding;
    const maxY = visibleBounds.maxY - newNodeHeight - padding;

    if (maxX >= minX && maxY >= minY) {
      const scanStepX = Math.max(42, Math.round(newNodeWidth * 0.32));
      const scanStepY = Math.max(42, Math.round(newNodeHeight * 0.32));

      for (let y = minY; y <= maxY; y += scanStepY) {
        for (let x = minX; x <= maxX; x += scanStepX) {
          evaluateCandidate(x, y);
        }
      }

      evaluateCandidate(minX, minY);
      evaluateCandidate(maxX, minY);
      evaluateCandidate(minX, maxY);
      evaluateCandidate(maxX, maxY);
    }
  }

  const resolvedCandidate = (bestInView || bestOutOfView) as
    | { x: number; y: number; score: number }
    | null;
  if (resolvedCandidate) {
    return { x: resolvedCandidate.x, y: resolvedCandidate.y };
  }

  return { x: anchorX + 2 * stepX, y: anchorY };
}
