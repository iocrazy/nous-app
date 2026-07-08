/**
 * Magnetic port snapping for drag-to-create (canvas-kit, clean-room).
 *
 * Pure geometry: given a release point and the candidate ports around it,
 * return the nearest port within a magnetic radius, or null when the point
 * landed in open space. Coordinates are flow-space; the radius is flow-space px.
 *
 * The 48px radius is the behavior spec — a release that lands within 48px of a
 * port completes the wire to it; anything further is treated as a drop into
 * empty canvas (which opens the create menu upstream).
 */

export interface SnapPort {
  /** Handle id on the target node (null for an unnamed/default handle). */
  id: string | null;
  /** Owning node id. */
  nodeId: string;
  /** Flow-space port position. */
  x: number;
  y: number;
}

export interface Point {
  x: number;
  y: number;
}

/** Default magnetic snap radius, flow-space px. */
export const DEFAULT_SNAP_RADIUS = 48;

/**
 * Nearest port to `point` within `radius`, or null when none qualifies.
 * On a distance tie the earlier port in `ports` wins (stable).
 */
export function nearestPort(
  point: Point,
  ports: SnapPort[],
  radius: number = DEFAULT_SNAP_RADIUS,
): SnapPort | null {
  let best: SnapPort | null = null;
  let bestDist = Infinity;
  for (const port of ports) {
    const dx = port.x - point.x;
    const dy = port.y - point.y;
    const dist = Math.hypot(dx, dy);
    if (dist > radius) continue;
    if (dist < bestDist) {
      best = port;
      bestDist = dist;
    }
  }
  return best;
}
