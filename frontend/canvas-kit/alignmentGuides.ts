/**
 * Alignment-guide math for the node canvas (Phase B Task 2).
 *
 * Pure geometry, no React/xyflow imports. Given the rect a user is dragging and
 * the rects of every other node, it finds the nearest edge alignment within a
 * tolerance and reports both the guide line to draw and the snapped coordinate
 * to seat the dragged rect on. Modelled on xyflow's MIT "helper lines" example
 * (its own idea, re-implemented here) — NOT ported from the Infinite-Canvas repo.
 *
 * Coordinates are flow-space, top-left origin. A rect's three x-edges are
 * left / centerX / right; its three y-edges are top / centerY / bottom. Any
 * dragged x-edge may snap to any other x-edge (9 combinations), likewise on y.
 */

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface AlignmentGuides {
  /** Flow-space x of the vertical guide line, when an x-edge aligned. */
  vertical?: number;
  /** Flow-space y of the horizontal guide line, when a y-edge aligned. */
  horizontal?: number;
  /** Dragged rect x that seats the matched edge on `vertical`. */
  snappedX?: number;
  /** Dragged rect y that seats the matched edge on `horizontal`. */
  snappedY?: number;
}

/** Default snap slack, in flow-space px. */
const DEFAULT_TOLERANCE = 5;

/** An edge of the dragged rect: its coordinate plus the offset from rect origin. */
interface DraggedEdge {
  coord: number;
  offset: number;
}

/** Best match found so far on one axis. */
interface AxisMatch {
  guide: number;
  snapped: number;
  delta: number;
}

/**
 * Find the nearest alignment (within `tolerance`) between the dragged edges and
 * every candidate edge. Returns the guide line coordinate and the rect origin
 * value that seats the matched edge on it, or null when nothing is close enough.
 */
function nearestMatch(
  draggedEdges: DraggedEdge[],
  candidateCoords: number[],
  tolerance: number,
): AxisMatch | null {
  let best: AxisMatch | null = null;
  for (const edge of draggedEdges) {
    for (const coord of candidateCoords) {
      const delta = Math.abs(edge.coord - coord);
      if (delta > tolerance) continue;
      if (best === null || delta < best.delta) {
        best = { guide: coord, snapped: coord - edge.offset, delta };
      }
    }
  }
  return best;
}

/** The three x-edge coordinates (left, centerX, right) of a rect. */
function xEdges(r: Rect): number[] {
  return [r.x, r.x + r.width / 2, r.x + r.width];
}

/** The three y-edge coordinates (top, centerY, bottom) of a rect. */
function yEdges(r: Rect): number[] {
  return [r.y, r.y + r.height / 2, r.y + r.height];
}

export function computeAlignmentGuides(
  dragged: Rect,
  others: Rect[],
  tolerance: number = DEFAULT_TOLERANCE,
): AlignmentGuides {
  const draggedX: DraggedEdge[] = [
    { coord: dragged.x, offset: 0 },
    { coord: dragged.x + dragged.width / 2, offset: dragged.width / 2 },
    { coord: dragged.x + dragged.width, offset: dragged.width },
  ];
  const draggedY: DraggedEdge[] = [
    { coord: dragged.y, offset: 0 },
    { coord: dragged.y + dragged.height / 2, offset: dragged.height / 2 },
    { coord: dragged.y + dragged.height, offset: dragged.height },
  ];

  const candidateX = others.flatMap(xEdges);
  const candidateY = others.flatMap(yEdges);

  const vertical = nearestMatch(draggedX, candidateX, tolerance);
  const horizontal = nearestMatch(draggedY, candidateY, tolerance);

  const guides: AlignmentGuides = {};
  if (vertical) {
    guides.vertical = vertical.guide;
    guides.snappedX = vertical.snapped;
  }
  if (horizontal) {
    guides.horizontal = horizontal.guide;
    guides.snappedY = horizontal.snapped;
  }
  return guides;
}
