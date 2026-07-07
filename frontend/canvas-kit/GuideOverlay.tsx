/**
 * GuideOverlay — alignment guide lines drawn over the node canvas (Phase B Task 2).
 *
 * Renders up to two thin lines (one vertical, one horizontal) at the flow-space
 * coordinates computed by `computeAlignmentGuides`, projected to screen space via
 * the live React Flow instance. It is a passive, pointer-events-none overlay: it
 * never intercepts drags. Colour comes from the editor's CSS variable system so
 * it themes with the rest of the v2 surface.
 */
import { useReactFlow } from '@xyflow/react';
import type { AlignmentGuides } from './alignmentGuides';
import './guideOverlay.css';

export interface GuideOverlayProps {
  guides: AlignmentGuides;
}

export function GuideOverlay({ guides }: GuideOverlayProps) {
  const { flowToScreenPosition } = useReactFlow();
  if (guides.vertical == null && guides.horizontal == null) return null;

  // Project the guide coordinate to screen space. The cross-axis value is
  // irrelevant for a full-length line, so 0 is fine as a placeholder.
  const vx = guides.vertical != null ? flowToScreenPosition({ x: guides.vertical, y: 0 }).x : null;
  const hy = guides.horizontal != null ? flowToScreenPosition({ x: 0, y: guides.horizontal }).y : null;

  return (
    <div className="mh-flow-guides" aria-hidden="true">
      {vx != null && <div className="mh-flow-guide mh-flow-guide-v" style={{ left: vx }} />}
      {hy != null && <div className="mh-flow-guide mh-flow-guide-h" style={{ top: hy }} />}
    </div>
  );
}
