// features/canvas-core/smart/nodes/NodeWidthGrip.tsx
//
// IC node-resize-handle: a VISIBLE bottom-right grip that drags the card
// width (output/media) or width+height (group). Hand-rolled pointer drag —
// RF's NodeResizeControl proved inert inside our node markup (2026-08-20
// "不可以拖动改变节点大小"), and a self-owned grip writes straight to node
// data with no RF measurement round-trip.

import { useRef } from 'react';

export function NodeWidthGrip({
  value,
  min,
  max = 1200,
  onChange,
  onHeightChange,
  heightValue,
  minHeight = 80,
}: {
  /** Current width (px). */
  value: number;
  min: number;
  max?: number;
  onChange: (w: number) => void;
  /** When set, the grip also drags height (group nodes). */
  onHeightChange?: (h: number) => void;
  heightValue?: number;
  minHeight?: number;
}) {
  const drag = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  return (
    <span
      data-testid="node-width-grip"
      role="presentation"
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.setPointerCapture?.(e.pointerId);
        drag.current = { x: e.clientX, y: e.clientY, w: value, h: heightValue ?? 0 };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        e.preventDefault();
        e.stopPropagation();
        onChange(Math.min(max, Math.max(min, Math.round(d.w + e.clientX - d.x))));
        if (onHeightChange)
          onHeightChange(Math.max(minHeight, Math.round(d.h + e.clientY - d.y)));
      }}
      onPointerUp={(e) => {
        drag.current = null;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      }}
      onPointerCancel={(e) => {
        drag.current = null;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      }}
      className="nodrag nopan absolute bottom-0 right-0 z-10 h-4 w-4 cursor-nwse-resize touch-none rounded-tl-md border-l border-t border-canvas-line bg-canvas-card/90 opacity-0 transition-opacity group-hover:opacity-100"
      title="Drag to resize"
    >
      <span className="pointer-events-none absolute bottom-0.5 right-0.5 h-1.5 w-1.5 border-b-2 border-r-2 border-canvas-muted" />
    </span>
  );
}
