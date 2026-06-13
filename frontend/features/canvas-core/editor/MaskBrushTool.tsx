/**
 * Brush-painting surface for the mask editor (Phase 3 Day 11).
 *
 * Renders an <img> plus an SVG overlay that visualises the painted
 * mask: brush strokes reveal a translucent indigo wash, eraser
 * strokes punch holes in it (via an SVG <mask> — brush strokes white,
 * eraser strokes black). State is controlled — the caller passes
 * `value` (strokes) and receives `onChange` with normalized points.
 *
 * The overlay viewBox is a fixed 1000×1000 with
 * preserveAspectRatio="none"; stroke widths are uniform in viewBox
 * units, so on non-square images the *preview* width of a stroke is
 * slightly anisotropic. The exported mask (maskExport.ts) rasterizes
 * at the source's true pixel size, so the persisted cutout is exact.
 */

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';

import {
  beginStroke,
  extendStroke,
  strokePath,
  type MaskStroke,
  type MaskTool,
} from './maskMath';

const VIEWBOX = 1000;

interface MaskBrushToolProps {
  /** Image to paint over. Anything <img> can render works. */
  src: string;
  /** Optional alt text. */
  alt?: string;
  /** Controlled strokes. */
  value: MaskStroke[];
  /** Fires on every paint tick. */
  onChange(next: MaskStroke[]): void;
  /** Active tool — strokes are recorded with this tool. */
  tool: MaskTool;
  /** Active brush size (fraction of image width). */
  brushSize: number;
  /** Reports the image's natural pixel size once it loads — the
   *  caller needs it to rasterize the mask at export time. */
  onNaturalSize?(size: { width: number; height: number }): void;
}

export function MaskBrushTool({
  src,
  alt = '',
  value,
  onChange,
  tool,
  brushSize,
  onNaturalSize,
}: MaskBrushToolProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const maskId = useId();
  const [painting, setPainting] = useState<{ pointerId: number } | null>(null);

  const toPoint = useCallback((event: { clientX: number; clientY: number }) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return null;
    return {
      x: (event.clientX - rect.left) / rect.width,
      y: (event.clientY - rect.top) / rect.height,
    };
  }, []);

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.stopPropagation();
      const point = toPoint(event);
      if (!point) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      setPainting({ pointerId: event.pointerId });
      onChange(beginStroke(value, tool, brushSize, point));
    },
    [toPoint, onChange, value, tool, brushSize],
  );

  useEffect(() => {
    if (!painting) return;
    const onMove = (event: PointerEvent) => {
      if (event.pointerId !== painting.pointerId) return;
      const point = toPoint(event);
      if (!point) return;
      onChange(extendStroke(value, point));
    };
    const stop = (event: PointerEvent) => {
      if (event.pointerId !== painting.pointerId) return;
      setPainting(null);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
    };
  }, [painting, toPoint, onChange, value]);

  const handleImageLoad = useCallback(
    (event: { currentTarget: HTMLImageElement }) => {
      const { naturalWidth, naturalHeight } = event.currentTarget;
      if (naturalWidth > 0 && naturalHeight > 0) {
        onNaturalSize?.({ width: naturalWidth, height: naturalHeight });
      }
    },
    [onNaturalSize],
  );

  return (
    <div
      ref={containerRef}
      data-testid="mask-brush-tool"
      onPointerDown={handlePointerDown}
      className="relative inline-block select-none"
      style={{ touchAction: 'none', cursor: 'crosshair' }}
    >
      <img
        src={src}
        alt={alt}
        draggable={false}
        onLoad={handleImageLoad}
        className="block max-h-[70vh] max-w-[80vw] object-contain"
      />
      <svg
        data-testid="mask-overlay"
        className="pointer-events-none absolute inset-0 h-full w-full"
        viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}
        preserveAspectRatio="none"
      >
        <mask id={maskId} maskUnits="userSpaceOnUse">
          <rect x="0" y="0" width={VIEWBOX} height={VIEWBOX} fill="black" />
          {value.map((stroke, index) => (
            <path
              key={index}
              data-testid={`mask-stroke-${index}`}
              data-tool={stroke.tool}
              d={strokePath(stroke.points, VIEWBOX, VIEWBOX)}
              fill="none"
              stroke={stroke.tool === 'brush' ? 'white' : 'black'}
              strokeWidth={stroke.size * VIEWBOX}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}
        </mask>
        <rect
          x="0"
          y="0"
          width={VIEWBOX}
          height={VIEWBOX}
          fill="rgba(99, 102, 241, 0.45)"
          mask={`url(#${maskId})`}
        />
      </svg>
    </div>
  );
}
