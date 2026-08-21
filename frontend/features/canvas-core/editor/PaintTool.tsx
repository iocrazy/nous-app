// features/canvas-core/editor/PaintTool.tsx
//
// IC-parity 画笔 mode (B2): free stroke / rect / ellipse / numbered label /
// text label, painted on an overlay above the image. Controlled like the
// sibling tools (src/value/onChange); baking into pixels happens in
// bakeAnnotations (imageBake.ts) at commit time.

import { useCallback, useRef, useState } from 'react';

import { mediaSrc } from '../smart/mediaUrl';

export type PaintShapeTool = 'free' | 'rect' | 'ellipse' | 'label' | 'text';

export interface PaintShape {
  tool: PaintShapeTool;
  color: string;
  size: number;
  /** free: polyline; rect/ellipse: [start, end]; label/text: [anchor]. */
  points: Array<{ x: number; y: number }>;
  /** label: its number; text: the typed text. */
  value?: string;
}

interface PaintToolProps {
  src: string;
  alt?: string;
  value: PaintShape[];
  onChange(next: PaintShape[]): void;
  tool: PaintShapeTool;
  color: string;
  size: number;
}

/** Fractional (0..1) coords relative to the image box. */
function fractionalPoint(e: React.PointerEvent, el: HTMLElement) {
  const rect = el.getBoundingClientRect();
  return {
    x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
  };
}

export function PaintTool({
  src,
  alt = '',
  value,
  onChange,
  tool,
  color,
  size,
}: PaintToolProps) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [drafting, setDrafting] = useState(false);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      const el = boxRef.current;
      if (!el) return;
      const p = fractionalPoint(e, el);
      if (tool === 'label') {
        const n = value.filter((s) => s.tool === 'label').length + 1;
        onChange([...value, { tool, color, size, points: [p], value: String(n) }]);
        return;
      }
      if (tool === 'text') {
        onChange([...value, { tool, color, size, points: [p], value: 'Double-click to edit' }]);
        return;
      }
      try {
        // Capture keeps the stroke alive outside the box — but on real
        // browsers this THROWS NotFoundError for non-capturable pointers
        // (observed on prod: the throw killed the handler BEFORE the shape
        // was added, so the brush never painted anything — 2026-08-21
        // "画笔画不出来". The stroke works without capture; onPointerLeave
        // already ends it at the edge.)
        (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      } catch {
        // non-capturable pointer — stroke continues uncaptured
      }
      setDrafting(true);
      onChange([...value, { tool, color, size, points: [p, p] }]);
    },
    [tool, color, size, value, onChange],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!drafting) return;
      const el = boxRef.current;
      if (!el) return;
      const p = fractionalPoint(e, el);
      const next = value.slice();
      const cur = next[next.length - 1];
      if (!cur) return;
      next[next.length - 1] =
        cur.tool === 'free'
          ? { ...cur, points: [...cur.points, p] }
          : { ...cur, points: [cur.points[0], p] };
      onChange(next);
    },
    [drafting, value, onChange],
  );

  const endStroke = useCallback(() => setDrafting(false), []);

  return (
    <div
      ref={boxRef}
      data-testid="paint-tool"
      className="relative inline-block max-h-full max-w-full cursor-crosshair select-none"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endStroke}
      onPointerLeave={endStroke}
    >
      <img
        src={mediaSrc(src)}
        alt={alt}
        className="pointer-events-none max-h-[60vh] max-w-full object-contain"
        draggable={false}
      />
      <svg className="pointer-events-none absolute inset-0 h-full w-full">
        {value.map((s, i) => (
          <ShapeSvg key={i} shape={s} />
        ))}
      </svg>
    </div>
  );
}

function ShapeSvg({ shape }: { shape: PaintShape }) {
  const pct = (v: number) => `${v * 100}%`;
  const stroke = shape.color;
  if (shape.tool === 'free') {
    return (
      <polyline
        points={shape.points.map((p) => `${p.x * 1000},${p.y * 1000}`).join(' ')}
        transform="scale(0.1)"
        fill="none"
        stroke={stroke}
        strokeWidth={shape.size}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    );
  }
  const [a, b = a] = shape.points;
  if (shape.tool === 'rect') {
    return (
      <rect
        x={pct(Math.min(a.x, b.x))}
        y={pct(Math.min(a.y, b.y))}
        width={pct(Math.abs(b.x - a.x))}
        height={pct(Math.abs(b.y - a.y))}
        fill="none"
        stroke={stroke}
        strokeWidth={shape.size / 4 + 1}
      />
    );
  }
  if (shape.tool === 'ellipse') {
    return (
      <ellipse
        cx={pct((a.x + b.x) / 2)}
        cy={pct((a.y + b.y) / 2)}
        rx={pct(Math.abs(b.x - a.x) / 2)}
        ry={pct(Math.abs(b.y - a.y) / 2)}
        fill="none"
        stroke={stroke}
        strokeWidth={shape.size / 4 + 1}
      />
    );
  }
  // label / text
  return (
    <g>
      {shape.tool === 'label' && (
        <circle cx={pct(a.x)} cy={pct(a.y)} r={12} fill={stroke} />
      )}
      <text
        x={pct(a.x)}
        y={pct(a.y)}
        dy={shape.tool === 'label' ? 4 : 0}
        dx={shape.tool === 'text' ? 6 : 0}
        textAnchor={shape.tool === 'label' ? 'middle' : 'start'}
        fill={shape.tool === 'label' ? '#fff' : stroke}
        fontSize={shape.tool === 'label' ? 12 : 14}
        fontWeight={700}
      >
        {shape.value}
      </text>
    </g>
  );
}
