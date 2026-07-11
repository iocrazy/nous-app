// features/canvas-core/smart/edges/SmartEdgeView.tsx
//
// Default smart-mode edge with a midpoint scissors (Infinite-Canvas parity
// G6 — the smart canvas conn-cut). Every edge carries a midpoint scissors,
// dimmed until hover/selection (Infinite's conn-cut opacity .55 → 1, P1-8);
// clicking removes the connection through setConnections
// (a user edit — undoable, unlike generation output). Rendering wraps the
// stock bezier path so the G10/G2 CSS chrome (faint→strong, run-state
// colouring) keeps applying — className styling targets the path as before.

import { BaseEdge, getBezierPath, type EdgeProps } from '@xyflow/react';
import { Scissors } from 'lucide-react';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';

export function SmartEdgeView(props: EdgeProps) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    selected,
    markerEnd,
    style,
  } = props;
  const setConnections = useCanvasCoreStore((s) => s.setConnections);

  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const cut = () => {
    const { connections } = useCanvasCoreStore.getState();
    const next = connections.filter(
      (c) => (c as Record<string, unknown>).id !== id,
    );
    // Legacy connections without a stored id get a synthesized RF id that
    // never matches — bail rather than commit a phantom undo step.
    if (next.length === connections.length) return;
    setConnections(next);
  };

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {/* foreignObject (not EdgeLabelRenderer) so the button lives inside
          the edge's own SVG — portal-free, works under jsdom too. Always
          rendered; the mh-edge-cut CSS dims it until hover/selection. */}
      <foreignObject
        width={24}
        height={24}
        x={labelX - 12}
        y={labelY - 12}
        className="overflow-visible"
      >
        <button
          type="button"
          className={`mh-edge-cut nodrag nopan flex h-6 w-6 items-center justify-center rounded-full border border-rose-400/60 bg-canvas-card text-rose-400 shadow hover:bg-rose-500 hover:text-white ${
            selected ? 'mh-edge-cut--active' : ''
          }`}
          onClick={cut}
          aria-label="Cut connection"
          title="Cut connection"
        >
          <Scissors size={11} />
        </button>
      </foreignObject>
    </>
  );
}
