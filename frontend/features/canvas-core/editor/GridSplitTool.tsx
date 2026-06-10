/**
 * Interactive split-line editor surface (Phase 3 Day 8).
 *
 * Renders an <img> plus one draggable line per grid split. Vertical
 * lines (xs) drag horizontally, horizontal lines (ys) drag
 * vertically; double-click removes a line. State is controlled — the
 * caller passes `value` and receives `onChange` with normalized
 * coordinates. gridMath.ts does the math; this file is wiring + DOM.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';

import {
  moveLine,
  removeLine,
  type GridAxis,
  type GridLines,
} from './gridMath';

interface GridSplitToolProps {
  /** Image to split. Anything <img> can render works (URL / data URL). */
  src: string;
  /** Optional alt text. */
  alt?: string;
  /** Controlled split lines. */
  value: GridLines;
  /** Fires on every drag tick and on line removal. */
  onChange(next: GridLines): void;
}

interface DragState {
  axis: GridAxis;
  index: number;
  pointerId: number;
  rectClientWidth: number;
  rectClientHeight: number;
}

export function GridSplitTool({
  src,
  alt = '',
  value,
  onChange,
}: GridSplitToolProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  const beginDrag = useCallback(
    (axis: GridAxis, index: number) =>
      (event: ReactPointerEvent<HTMLDivElement>) => {
        event.preventDefault();
        event.stopPropagation();
        const rect = containerRef.current?.getBoundingClientRect();
        if (!rect) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        setDrag({
          axis,
          index,
          pointerId: event.pointerId,
          rectClientWidth: rect.width,
          rectClientHeight: rect.height,
        });
      },
    [],
  );

  useEffect(() => {
    if (!drag) return;
    const onMove = (event: PointerEvent) => {
      if (event.pointerId !== drag.pointerId) return;
      // Convert pixel delta to normalized delta against the rendered
      // image rect (not the source bitmap).
      const current =
        drag.axis === 'x' ? value.xs[drag.index] : value.ys[drag.index];
      if (current === undefined) return;
      const delta =
        drag.axis === 'x'
          ? event.movementX / drag.rectClientWidth
          : event.movementY / drag.rectClientHeight;
      onChange(moveLine(value, drag.axis, drag.index, current + delta));
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

  const handleRemove = useCallback(
    (axis: GridAxis, index: number) => () => {
      onChange(removeLine(value, axis, index));
    },
    [onChange, value],
  );

  return (
    <div
      ref={containerRef}
      data-testid="grid-split-tool"
      className="relative inline-block select-none"
    >
      <img
        src={src}
        alt={alt}
        draggable={false}
        className="block max-h-[70vh] max-w-[80vw] object-contain"
      />
      {value.xs.map((x, index) => (
        <div
          key={`x-${index}`}
          data-testid={`grid-line-x-${index}`}
          role="separator"
          aria-orientation="vertical"
          aria-label={`Vertical split line ${index + 1}`}
          onPointerDown={beginDrag('x', index)}
          onDoubleClick={handleRemove('x', index)}
          // The visible 2px line sits inside a wider hit area so the
          // grab target isn't pixel-perfect.
          className="absolute top-0 h-full w-3 -translate-x-1/2 cursor-ew-resize"
          style={{ left: `${x * 100}%`, touchAction: 'none' }}
        >
          <div className="mx-auto h-full w-0.5 bg-indigo-400 shadow-[0_0_0_1px_rgba(0,0,0,0.4)]" />
        </div>
      ))}
      {value.ys.map((y, index) => (
        <div
          key={`y-${index}`}
          data-testid={`grid-line-y-${index}`}
          role="separator"
          aria-orientation="horizontal"
          aria-label={`Horizontal split line ${index + 1}`}
          onPointerDown={beginDrag('y', index)}
          onDoubleClick={handleRemove('y', index)}
          className="absolute left-0 flex h-3 w-full -translate-y-1/2 cursor-ns-resize items-center"
          style={{ top: `${y * 100}%`, touchAction: 'none' }}
        >
          <div className="h-0.5 w-full bg-indigo-400 shadow-[0_0_0_1px_rgba(0,0,0,0.4)]" />
        </div>
      ))}
    </div>
  );
}
