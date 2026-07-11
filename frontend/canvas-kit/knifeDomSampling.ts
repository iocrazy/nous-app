// canvas-kit/knifeDomSampling.ts
//
// DOM adapters for KnifeOverlay: sample the RENDERED React Flow edges and
// nodes into overlay-local screen coordinates. Kept apart from the overlay
// so the interaction logic stays jsdom-testable (jsdom has no SVG path
// length API); this file is covered by the real-browser e2e.

import type { EdgeSample, NodeSample } from './KnifeOverlay';
import type { Point } from './knifeGeometry';

const SAMPLES_PER_EDGE = 24;

/** Sample every rendered edge path into an overlay-local polyline. */
export function sampleEdgesFromDom(container: HTMLElement): EdgeSample[] {
  const origin = container.getBoundingClientRect();
  const out: EdgeSample[] = [];
  const edges = container.querySelectorAll<SVGGElement>('.react-flow__edge');
  edges.forEach((edgeEl) => {
    const id = edgeEl.getAttribute('data-id');
    const path = edgeEl.querySelector<SVGGeometryElement>('.react-flow__edge-path');
    if (!id || !path || typeof path.getTotalLength !== 'function') return;
    const total = path.getTotalLength();
    const ctm = path.getScreenCTM();
    if (!ctm || total === 0) return;
    const points: Point[] = [];
    for (let i = 0; i <= SAMPLES_PER_EDGE; i++) {
      const p = path.getPointAtLength((total * i) / SAMPLES_PER_EDGE);
      points.push({
        x: ctm.a * p.x + ctm.c * p.y + ctm.e - origin.left,
        y: ctm.b * p.x + ctm.d * p.y + ctm.f - origin.top,
      });
    }
    out.push({ id, points });
  });
  return out;
}

/** Overlay-local bounding rects of every rendered node. */
export function sampleNodesFromDom(container: HTMLElement): NodeSample[] {
  const origin = container.getBoundingClientRect();
  const out: NodeSample[] = [];
  container.querySelectorAll<HTMLElement>('.react-flow__node').forEach((el) => {
    const id = el.getAttribute('data-id');
    if (!id) return;
    const r = el.getBoundingClientRect();
    out.push({
      id,
      rect: { x: r.left - origin.left, y: r.top - origin.top, width: r.width, height: r.height },
    });
  });
  return out;
}
