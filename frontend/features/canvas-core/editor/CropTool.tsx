/**
 * Interactive crop selector (Phase 3 Day 1).
 *
 * Renders an <img>, an overlay shading the discarded area, and the
 * 8 resize handles + the rectangle's interior for "move". State is
 * controlled — the caller passes `value` and gets `onChange` callbacks
 * with normalized coordinates. A "Commit" affordance is delegated to
 * the parent; this component is the editor surface only.
 *
 * Coordinates are normalized [0, 1] so the geometry is independent
 * of the displayed image size. cropMath.ts does the math; this file
 * is wiring + DOM.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';

import { dragHandle } from './cropMath';
import { FULL_REGION, type CropHandle, type CropRegion } from './types';

interface CropToolProps {
  /** Image to crop. Anything <img> can render works (URL / data URL). */
  src: string;
  /** Optional alt text. */
  alt?: string;
  /** Controlled region. */
  value: CropRegion;
  /** Fires on every drag tick. */
  onChange(next: CropRegion): void;
}

interface DragState {
  handle: CropHandle;
  start: CropRegion;
  pointerId: number;
  rectClientWidth: number;
  rectClientHeight: number;
}

const HANDLES: ReadonlyArray<{
  id: CropHandle;
  pos: string;
  cursor: string;
}> = [
  { id: 'nw', pos: 'left-0 top-0 -translate-x-1/2 -translate-y-1/2', cursor: 'nwse-resize' },
  { id: 'n', pos: 'left-1/2 top-0 -translate-x-1/2 -translate-y-1/2', cursor: 'ns-resize' },
  { id: 'ne', pos: 'right-0 top-0 translate-x-1/2 -translate-y-1/2', cursor: 'nesw-resize' },
  { id: 'e', pos: 'right-0 top-1/2 translate-x-1/2 -translate-y-1/2', cursor: 'ew-resize' },
  { id: 'se', pos: 'right-0 bottom-0 translate-x-1/2 translate-y-1/2', cursor: 'nwse-resize' },
  { id: 's', pos: 'left-1/2 bottom-0 -translate-x-1/2 translate-y-1/2', cursor: 'ns-resize' },
  { id: 'sw', pos: 'left-0 bottom-0 -translate-x-1/2 translate-y-1/2', cursor: 'nesw-resize' },
  { id: 'w', pos: 'left-0 top-1/2 -translate-x-1/2 -translate-y-1/2', cursor: 'ew-resize' },
];

export function CropTool({
  src,
  alt = '',
  value = FULL_REGION,
  onChange,
}: CropToolProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  const beginDrag = useCallback(
    (handle: CropHandle) => (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.stopPropagation();
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const target = event.currentTarget;
      target.setPointerCapture(event.pointerId);
      setDrag({
        handle,
        start: value,
        pointerId: event.pointerId,
        rectClientWidth: rect.width,
        rectClientHeight: rect.height,
      });
    },
    [value],
  );

  useEffect(() => {
    if (!drag) return;
    const onMove = (event: PointerEvent) => {
      if (event.pointerId !== drag.pointerId) return;
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      // Convert pixel delta to normalized delta against the rendered
      // image rect (not the source bitmap — the rectangle is anchored
      // to the visible area).
      const dxNorm = event.movementX / drag.rectClientWidth;
      const dyNorm = event.movementY / drag.rectClientHeight;
      const next = dragHandle(value, drag.handle, { dx: dxNorm, dy: dyNorm });
      onChange(next);
    };
    const stop = (event: PointerEvent) => {
      if (event.pointerId !== drag.pointerId) return;
      setDrag(null);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
    };
  }, [drag, onChange, value]);

  return (
    <div
      ref={containerRef}
      data-testid="crop-tool"
      className="relative inline-block select-none"
    >
      <img
        src={src}
        alt={alt}
        draggable={false}
        className="block max-h-[70vh] max-w-[80vw] object-contain"
      />
      <div
        // The semi-transparent shading outside the crop rect is drawn
        // with a single inset box-shadow on the rectangle itself.
        className="pointer-events-none absolute"
        style={{
          left: `${value.x * 100}%`,
          top: `${value.y * 100}%`,
          width: `${value.width * 100}%`,
          height: `${value.height * 100}%`,
          boxShadow: '0 0 0 9999px rgba(0,0,0,0.5)',
        }}
      />
      <div
        className="absolute border-2 border-white"
        style={{
          left: `${value.x * 100}%`,
          top: `${value.y * 100}%`,
          width: `${value.width * 100}%`,
          height: `${value.height * 100}%`,
        }}
        role="region"
        aria-label="Crop selection"
      >
        <div
          data-testid="crop-move"
          onPointerDown={beginDrag('move')}
          className="absolute inset-0 cursor-move"
        />
        {HANDLES.map((h) => (
          <div
            key={h.id}
            data-testid={`crop-handle-${h.id}`}
            onPointerDown={beginDrag(h.id)}
            className={`absolute h-3 w-3 rounded-sm border border-white bg-indigo-500 ${h.pos}`}
            style={{ cursor: h.cursor, touchAction: 'none' }}
          />
        ))}
      </div>
    </div>
  );
}
