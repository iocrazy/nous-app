import React, { useState, useRef, useCallback, useEffect } from 'react';
import { ArrowUpRight, Square, Pencil, Type, Undo2, Check, X } from 'lucide-react';

// All coordinates are normalized 0~1
export interface NormalizedAnnotation {
  tool_type: 'arrow' | 'rect' | 'freehand' | 'text';
  data: Record<string, unknown>;
}

type DrawTool = 'arrow' | 'rect' | 'freehand' | 'text';

interface ResourceAnnotationOverlayProps {
  isActive: boolean;
  viewAnnotations?: NormalizedAnnotation[];
  onDone: (annotations: NormalizedAnnotation[]) => void;
  onCancel: () => void;
}

const COLORS = ['#ff4444', '#ffbb33', '#33cc33', '#4488ff', '#ffffff'];
const TOOL_ITEMS: { id: DrawTool; icon: React.FC<{ size?: number }>; label: string }[] = [
  { id: 'arrow', icon: ArrowUpRight, label: 'Arrow' },
  { id: 'rect', icon: Square, label: 'Rectangle' },
  { id: 'freehand', icon: Pencil, label: 'Freehand' },
  { id: 'text', icon: Type, label: 'Text' },
];

// ─── SVG renderers ──────────────────────────────────────

function renderAnnotation(a: NormalizedAnnotation, w: number, h: number, idx: number) {
  const d = a.data as Record<string, number | string | number[][]>;
  const color = (d.color as string) || '#ff4444';
  const strokeW = (d.width as number) || 2;

  switch (a.tool_type) {
    case 'arrow': {
      const x1 = (d.x1 as number) * w, y1 = (d.y1 as number) * h;
      const x2 = (d.x2 as number) * w, y2 = (d.y2 as number) * h;
      const angle = Math.atan2(y2 - y1, x2 - x1);
      const hl = 12;
      return (
        <g key={idx}>
          <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={strokeW} strokeLinecap="round" />
          <line x1={x2} y1={y2} x2={x2 - hl * Math.cos(angle - Math.PI / 6)} y2={y2 - hl * Math.sin(angle - Math.PI / 6)} stroke={color} strokeWidth={strokeW} strokeLinecap="round" />
          <line x1={x2} y1={y2} x2={x2 - hl * Math.cos(angle + Math.PI / 6)} y2={y2 - hl * Math.sin(angle + Math.PI / 6)} stroke={color} strokeWidth={strokeW} strokeLinecap="round" />
        </g>
      );
    }
    case 'rect': {
      const rx = (d.x as number) * w, ry = (d.y as number) * h;
      const rw = (d.w as number) * w, rh = (d.h as number) * h;
      return <rect key={idx} x={rx} y={ry} width={rw} height={rh} stroke={color} strokeWidth={strokeW} fill="none" rx={2} />;
    }
    case 'freehand': {
      const pts = d.points as number[][];
      if (!pts || pts.length < 2) return null;
      const pathD = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0] * w},${p[1] * h}`).join(' ');
      return <path key={idx} d={pathD} stroke={color} strokeWidth={strokeW} fill="none" strokeLinecap="round" strokeLinejoin="round" />;
    }
    case 'text': {
      const tx = (d.x as number) * w, ty = (d.y as number) * h;
      const text = (d.text as string) || '';
      const fs = (d.fontSize as number) || 14;
      return <text key={idx} x={tx} y={ty} fill={color} fontSize={fs} fontFamily="sans-serif">{text}</text>;
    }
    default: return null;
  }
}

// ─── Inline Toolbar ─────────────────────────────────────

const InlineToolbar: React.FC<{
  tool: DrawTool;
  color: string;
  strokeWidth: number;
  canUndo: boolean;
  onToolChange: (t: DrawTool) => void;
  onColorChange: (c: string) => void;
  onStrokeWidthChange: (w: number) => void;
  onUndo: () => void;
  onDone: () => void;
  onCancel: () => void;
}> = ({ tool, color, strokeWidth, canUndo, onToolChange, onColorChange, onStrokeWidthChange, onUndo, onDone, onCancel }) => (
  <div className="flex items-center gap-2.5 px-3 py-2 bg-zinc-900/95 backdrop-blur border border-zinc-700 rounded-xl shadow-2xl">
    <div className="flex items-center gap-0.5">
      {TOOL_ITEMS.map(({ id, icon: Icon, label }) => (
        <button key={id} onClick={() => onToolChange(id)} title={label}
          className={`p-1.5 rounded-lg transition-colors ${tool === id ? 'bg-indigo-500/20 text-indigo-400' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'}`}
        ><Icon size={16} /></button>
      ))}
    </div>
    <div className="w-px h-5 bg-zinc-700" />
    <div className="flex items-center gap-1">
      {COLORS.map((c) => (
        <button key={c} onClick={() => onColorChange(c)}
          className={`w-4.5 h-4.5 rounded-full border-2 transition-all ${color === c ? 'border-white scale-110' : 'border-transparent hover:border-zinc-500'}`}
          style={{ backgroundColor: c, width: 18, height: 18 }}
        />
      ))}
    </div>
    <div className="w-px h-5 bg-zinc-700" />
    <div className="flex items-center gap-0.5">
      {[2, 4].map((w) => (
        <button key={w} onClick={() => onStrokeWidthChange(w)}
          className={`px-1.5 py-1 rounded transition-colors ${strokeWidth === w ? 'bg-zinc-700 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}
        ><div className="rounded-full bg-current" style={{ width: 14, height: w }} /></button>
      ))}
    </div>
    <div className="w-px h-5 bg-zinc-700" />
    <button onClick={onUndo} disabled={!canUndo} title="Undo"
      className={`p-1.5 rounded-lg transition-colors ${canUndo ? 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800' : 'text-zinc-700 cursor-not-allowed'}`}
    ><Undo2 size={16} /></button>
    <button onClick={onDone} className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg transition-colors">
      <Check size={14} /> Done
    </button>
    <button onClick={onCancel} className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-lg transition-colors" title="Cancel">
      <X size={16} />
    </button>
  </div>
);

// ─── Main Overlay ───────────────────────────────────────

export const ResourceAnnotationOverlay: React.FC<ResourceAnnotationOverlayProps> = ({
  isActive,
  viewAnnotations,
  onDone,
  onCancel,
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const [tool, setTool] = useState<DrawTool>('arrow');
  const [color, setColor] = useState('#ff4444');
  const [strokeWidth, setStrokeWidth] = useState(2);
  const [annotations, setAnnotations] = useState<NormalizedAnnotation[]>([]);
  const [isDrawing, setIsDrawing] = useState(false);
  const [startPoint, setStartPoint] = useState<{ x: number; y: number } | null>(null);
  const [currentPoint, setCurrentPoint] = useState<{ x: number; y: number } | null>(null);
  const [freehandPoints, setFreehandPoints] = useState<number[][]>([]);

  const getNormalized = useCallback((e: React.MouseEvent): { x: number; y: number } => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)),
    };
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (!isActive) return;
    e.preventDefault();
    e.stopPropagation();
    const pt = getNormalized(e);
    setStartPoint(pt);
    setCurrentPoint(pt);
    setIsDrawing(true);
    if (tool === 'freehand') setFreehandPoints([[pt.x, pt.y]]);
  }, [isActive, tool, getNormalized]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isDrawing || !isActive) return;
    e.preventDefault();
    const pt = getNormalized(e);
    setCurrentPoint(pt);
    if (tool === 'freehand') setFreehandPoints((prev) => [...prev, [pt.x, pt.y]]);
  }, [isDrawing, isActive, tool, getNormalized]);

  const handleMouseUp = useCallback((e: React.MouseEvent) => {
    if (!isDrawing || !startPoint || !currentPoint) return;
    e.preventDefault();
    setIsDrawing(false);

    let ann: NormalizedAnnotation | null = null;
    switch (tool) {
      case 'arrow':
        ann = { tool_type: 'arrow', data: { x1: startPoint.x, y1: startPoint.y, x2: currentPoint.x, y2: currentPoint.y, color, width: strokeWidth } };
        break;
      case 'rect': {
        const x = Math.min(startPoint.x, currentPoint.x), y = Math.min(startPoint.y, currentPoint.y);
        const w = Math.abs(currentPoint.x - startPoint.x), h = Math.abs(currentPoint.y - startPoint.y);
        if (w > 0.01 && h > 0.01) ann = { tool_type: 'rect', data: { x, y, w, h, color, width: strokeWidth } };
        break;
      }
      case 'freehand':
        if (freehandPoints.length >= 2) ann = { tool_type: 'freehand', data: { points: freehandPoints, color, width: strokeWidth } };
        break;
      case 'text': {
        const text = prompt('Enter text:');
        if (text) ann = { tool_type: 'text', data: { x: startPoint.x, y: startPoint.y, text, color, fontSize: 14 } };
        break;
      }
    }
    if (ann) setAnnotations((prev) => [...prev, ann!]);
    setStartPoint(null);
    setCurrentPoint(null);
    setFreehandPoints([]);
  }, [isDrawing, startPoint, currentPoint, tool, color, strokeWidth, freehandPoints]);

  const handleUndo = useCallback(() => setAnnotations((prev) => prev.slice(0, -1)), []);
  const handleDone = useCallback(() => { onDone(annotations); setAnnotations([]); }, [annotations, onDone]);
  const handleCancel = useCallback(() => { setAnnotations([]); onCancel(); }, [onCancel]);

  const [dims, setDims] = useState({ w: 1920, h: 1080 });
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const update = () => {
      const rect = svg.getBoundingClientRect();
      if (rect.width > 0) setDims({ w: rect.width, h: rect.height });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(svg);
    return () => ro.disconnect();
  }, []);

  const displayAnnotations = isActive ? annotations : (viewAnnotations || []);

  const previewAnnotation: NormalizedAnnotation | null =
    isDrawing && startPoint && currentPoint
      ? tool === 'freehand'
        ? { tool_type: 'freehand', data: { points: freehandPoints, color, width: strokeWidth } }
        : tool === 'arrow'
        ? { tool_type: 'arrow', data: { x1: startPoint.x, y1: startPoint.y, x2: currentPoint.x, y2: currentPoint.y, color, width: strokeWidth } }
        : tool === 'rect'
        ? { tool_type: 'rect', data: { x: Math.min(startPoint.x, currentPoint.x), y: Math.min(startPoint.y, currentPoint.y), w: Math.abs(currentPoint.x - startPoint.x), h: Math.abs(currentPoint.y - startPoint.y), color, width: strokeWidth } }
        : null
      : null;

  if (!isActive && (!viewAnnotations || viewAnnotations.length === 0)) return null;

  return (
    <div className="absolute inset-0 z-20" style={{ pointerEvents: isActive ? 'auto' : 'none' }}>
      {isActive && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-30">
          <InlineToolbar
            tool={tool} color={color} strokeWidth={strokeWidth} canUndo={annotations.length > 0}
            onToolChange={setTool} onColorChange={setColor} onStrokeWidthChange={setStrokeWidth}
            onUndo={handleUndo} onDone={handleDone} onCancel={handleCancel}
          />
        </div>
      )}
      <svg ref={svgRef} className="absolute inset-0 w-full h-full"
        style={{ cursor: isActive ? (tool === 'text' ? 'text' : 'crosshair') : 'default' }}
        onMouseDown={handleMouseDown} onMouseMove={handleMouseMove} onMouseUp={handleMouseUp}
      >
        {displayAnnotations.map((a, i) => renderAnnotation(a, dims.w, dims.h, i))}
        {previewAnnotation && renderAnnotation(previewAnnotation, dims.w, dims.h, -1)}
      </svg>
    </div>
  );
};
