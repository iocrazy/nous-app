/**
 * Drag-to-extend surface for the outpaint editor (Phase 3 Day 14).
 *
 * Renders the TARGET canvas (dashed frame) with the source image
 * inset according to the current padding; the padded area previews
 * the blur fill via a scaled blurred copy of the image. Each edge
 * has a drag handle — dragging outward grows that side's padding.
 *
 * State is controlled: `value` (padding) in + `onChange` out. Drag
 * deltas are normalized against the *image's* rendered size at drag
 * start so 100px of drag equals the same padding regardless of how
 * far the canvas has already grown.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';

import {
  setSide,
  type OutpaintPadding,
  type OutpaintSide,
} from './outpaintMath';

interface OutpaintToolProps {
  src: string;
  alt?: string;
  value: OutpaintPadding;
  onChange(next: OutpaintPadding): void;
  /** Reports the image's natural pixel size once loaded (for the
   *  live resolution readout in the modal). */
  onNaturalSize?(size: { width: number; height: number }): void;
}

interface DragState {
  side: OutpaintSide;
  pointerId: number;
  /** Image rendered px at drag start — the normalization basis. */
  imageWidth: number;
  imageHeight: number;
  startValue: number;
  startClientX: number;
  startClientY: number;
}

const HANDLES: ReadonlyArray<{
  side: OutpaintSide;
  pos: string;
  cursor: string;
}> = [
  { side: 'left', pos: 'left-0 top-1/2 -translate-x-1/2 -translate-y-1/2', cursor: 'ew-resize' },
  { side: 'right', pos: 'right-0 top-1/2 translate-x-1/2 -translate-y-1/2', cursor: 'ew-resize' },
  { side: 'top', pos: 'top-0 left-1/2 -translate-x-1/2 -translate-y-1/2', cursor: 'ns-resize' },
  { side: 'bottom', pos: 'bottom-0 left-1/2 -translate-x-1/2 translate-y-1/2', cursor: 'ns-resize' },
];

export function OutpaintTool({
  src,
  alt = '',
  value,
  onChange,
  onNaturalSize,
}: OutpaintToolProps) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  const beginDrag = useCallback(
    (side: OutpaintSide) => (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.stopPropagation();
      const rect = imageRef.current?.getBoundingClientRect();
      if (!rect || rect.width === 0 || rect.height === 0) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      setDrag({
        side,
        pointerId: event.pointerId,
        imageWidth: rect.width,
        imageHeight: rect.height,
        startValue: value[side],
        startClientX: event.clientX,
        startClientY: event.clientY,
      });
    },
    [value],
  );

  useEffect(() => {
    if (!drag) return;
    const onMove = (event: PointerEvent) => {
      if (event.pointerId !== drag.pointerId) return;
      // Outward = positive padding. Left/top handles extend when
      // dragged toward negative client coordinates.
      let delta: number;
      switch (drag.side) {
        case 'left':
          delta = (drag.startClientX - event.clientX) / drag.imageWidth;
          break;
        case 'right':
          delta = (event.clientX - drag.startClientX) / drag.imageWidth;
          break;
        case 'top':
          delta = (drag.startClientY - event.clientY) / drag.imageHeight;
          break;
        default:
          delta = (event.clientY - drag.startClientY) / drag.imageHeight;
      }
      onChange(setSide(value, drag.side, drag.startValue + delta));
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

  const handleImageLoad = useCallback(
    (event: { currentTarget: HTMLImageElement }) => {
      const { naturalWidth, naturalHeight } = event.currentTarget;
      if (naturalWidth > 0 && naturalHeight > 0) {
        onNaturalSize?.({ width: naturalWidth, height: naturalHeight });
      }
    },
    [onNaturalSize],
  );

  // The image occupies 1/(1+l+r) of the target width, offset by
  // l/(1+l+r) — same proportions the backend will produce.
  const totalX = 1 + value.left + value.right;
  const totalY = 1 + value.top + value.bottom;
  const imageStyle = {
    left: `${(value.left / totalX) * 100}%`,
    top: `${(value.top / totalY) * 100}%`,
    width: `${(1 / totalX) * 100}%`,
    height: `${(1 / totalY) * 100}%`,
  };

  return (
    <div
      data-testid="outpaint-tool"
      className="relative inline-block select-none"
    >
      <div
        data-testid="outpaint-canvas"
        className="relative max-h-[65vh] max-w-[78vw] overflow-hidden rounded border-2 border-dashed border-indigo-400 bg-slate-100 dark:bg-slate-800"
        style={{ width: 520, aspectRatio: `${totalX} / ${totalY}` }}
      >
        {/* Blurred preview of the fill area. */}
        <img
          src={src}
          alt=""
          aria-hidden="true"
          draggable={false}
          className="absolute inset-0 h-full w-full object-fill opacity-70 blur-xl"
        />
        <img
          ref={imageRef}
          src={src}
          alt={alt}
          draggable={false}
          onLoad={handleImageLoad}
          data-testid="outpaint-source-image"
          className="absolute object-fill"
          style={imageStyle}
        />
        {HANDLES.map((handle) => (
          <div
            key={handle.side}
            data-testid={`outpaint-handle-${handle.side}`}
            role="slider"
            aria-label={`Extend ${handle.side}`}
            aria-valuenow={Math.round(value[handle.side] * 100)}
            onPointerDown={beginDrag(handle.side)}
            className={`absolute z-10 h-4 w-4 rounded-full border-2 border-white bg-indigo-500 shadow ${handle.pos}`}
            style={{ cursor: handle.cursor, touchAction: 'none' }}
          />
        ))}
      </div>
    </div>
  );
}
