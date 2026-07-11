// canvas-kit/knifeGeometry.ts
//
// Knife-cut geometry (Infinite-Canvas parity — canvas.js segmentsIntersect
// / pointSegmentDistance / knifeHitsConnection, translated verbatim). Pure
// math over screen-space points; the DOM sampling of edge paths lives in
// the overlay component, keeping this module trivially unit-testable.

export interface Point {
  x: number;
  y: number;
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Classic orientation test with collinear-overlap handling. */
export function segmentsIntersect(a: Point, b: Point, c: Point, d: Point): boolean {
  const orient = (p: Point, q: Point, r: Point) =>
    (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  const onSeg = (p: Point, q: Point, r: Point) =>
    Math.min(p.x, r.x) <= q.x &&
    q.x <= Math.max(p.x, r.x) &&
    Math.min(p.y, r.y) <= q.y &&
    q.y <= Math.max(p.y, r.y);
  const o1 = orient(a, b, c);
  const o2 = orient(a, b, d);
  const o3 = orient(c, d, a);
  const o4 = orient(c, d, b);
  if (o1 === 0 && onSeg(a, c, b)) return true;
  if (o2 === 0 && onSeg(a, d, b)) return true;
  if (o3 === 0 && onSeg(c, a, d)) return true;
  if (o4 === 0 && onSeg(c, b, d)) return true;
  return (o1 > 0) !== (o2 > 0) && (o3 > 0) !== (o4 > 0);
}

/** Distance from a point to a segment (clamped to endpoints). */
export function pointSegmentDistance(p: Point, a: Point, b: Point): number {
  const abx = b.x - a.x;
  const aby = b.y - a.y;
  const lenSq = abx * abx + aby * aby;
  const t = lenSq === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * abx + (p.y - a.y) * aby) / lenSq));
  const cx = a.x + t * abx;
  const cy = a.y + t * aby;
  return Math.hypot(p.x - cx, p.y - cy);
}

/** Does the knife segment a→b hit a sampled edge polyline? Mirrors
 *  Infinite's knifeHitsConnection: true intersection OR either sample
 *  endpoint within `threshold` (zoom-out forgiveness). */
export function segmentHitsPolyline(
  a: Point,
  b: Point,
  polyline: Point[],
  threshold: number,
): boolean {
  for (let i = 1; i < polyline.length; i++) {
    const prev = polyline[i - 1];
    const cur = polyline[i];
    if (
      segmentsIntersect(a, b, prev, cur) ||
      pointSegmentDistance(prev, a, b) <= threshold ||
      pointSegmentDistance(cur, a, b) <= threshold
    ) {
      return true;
    }
  }
  return false;
}

/** Segment vs axis-aligned rect (node bodies exclude their edges from a
 *  cut that merely passes through the node). */
export function segmentIntersectsRect(a: Point, b: Point, r: Rect): boolean {
  const inside = (p: Point) =>
    p.x >= r.x && p.x <= r.x + r.width && p.y >= r.y && p.y <= r.y + r.height;
  if (inside(a) || inside(b)) return true;
  const p1 = { x: r.x, y: r.y };
  const p2 = { x: r.x + r.width, y: r.y };
  const p3 = { x: r.x + r.width, y: r.y + r.height };
  const p4 = { x: r.x, y: r.y + r.height };
  return (
    segmentsIntersect(a, b, p1, p2) ||
    segmentsIntersect(a, b, p2, p3) ||
    segmentsIntersect(a, b, p3, p4) ||
    segmentsIntersect(a, b, p4, p1)
  );
}
