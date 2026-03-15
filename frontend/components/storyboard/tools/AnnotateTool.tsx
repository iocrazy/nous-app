import React, { useState, useCallback, useRef } from 'react';
import Konva from 'konva';
import { Stage, Layer, Line, Rect, Circle, Text, Arrow } from 'react-konva';
import { X, Undo2 } from 'lucide-react';

// ─── Types ────────────────────────────────────────────────────────────────────

type DrawTool = 'pen' | 'line' | 'rect' | 'circle' | 'text' | 'arrow';
type LineWidth = 'thin' | 'medium' | 'thick';

interface AnnotationShape {
  id: string;
  tool: DrawTool;
  color: string;
  lineWidth: number;
  points?: number[];
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  radius?: number;
  text?: string;
}

interface AnnotateToolProps {
  imageUrl: string;
  initialAnnotations?: AnnotationShape[];
  onDone: (annotations: AnnotationShape[]) => void;
  onCancel: () => void;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const PRESET_COLORS = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#a855f7'];
const LINE_WIDTHS: Record<LineWidth, number> = { thin: 2, medium: 4, thick: 8 };
const TOOL_ICONS: Record<DrawTool, string> = {
  pen: 'Pen',
  line: 'Line',
  rect: 'Rect',
  circle: 'Circle',
  text: 'Text',
  arrow: 'Arrow',
};

const CANVAS_W = 640;
const CANVAS_H = 400;

// ─── Component ────────────────────────────────────────────────────────────────

const AnnotateTool = React.memo(function AnnotateTool({
  imageUrl,
  initialAnnotations = [],
  onDone,
  onCancel,
}: AnnotateToolProps) {
  const [tool, setTool] = useState<DrawTool>('pen');
  const [color, setColor] = useState(PRESET_COLORS[0]);
  const [customColor, setCustomColor] = useState('#ffffff');
  const [lineWidthKey, setLineWidthKey] = useState<LineWidth>('medium');
  const [shapes, setShapes] = useState<AnnotationShape[]>(initialAnnotations);
  const [drawing, setDrawing] = useState(false);
  const [currentShape, setCurrentShape] = useState<AnnotationShape | null>(null);

  const stageRef = useRef<Konva.Stage>(null);

  const lineWidth = LINE_WIDTHS[lineWidthKey];

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const getPos = useCallback((e: any): { x: number; y: number } => {
    const stage = e.target.getStage?.() as Konva.Stage | null;
    return stage?.getPointerPosition() ?? { x: 0, y: 0 };
  }, []);

  const handleMouseDown = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (e: any) => {
      const pos = getPos(e);
      const id = `shape-${Date.now()}`;

      if (tool === 'pen') {
        setCurrentShape({ id, tool, color, lineWidth, points: [pos.x, pos.y] });
      } else if (tool === 'line' || tool === 'arrow') {
        setCurrentShape({ id, tool, color, lineWidth, points: [pos.x, pos.y, pos.x, pos.y] });
      } else if (tool === 'rect') {
        setCurrentShape({ id, tool, color, lineWidth, x: pos.x, y: pos.y, width: 0, height: 0 });
      } else if (tool === 'circle') {
        setCurrentShape({ id, tool, color, lineWidth, x: pos.x, y: pos.y, radius: 0 });
      } else if (tool === 'text') {
        const text = window.prompt('Enter text:') ?? '';
        if (text) {
          setShapes((prev) => [...prev, { id, tool, color, lineWidth, x: pos.x, y: pos.y, text }]);
        }
        return;
      }
      setDrawing(true);
    },
    [tool, color, lineWidth, getPos]
  );

  const handleMouseMove = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (e: any) => {
      if (!drawing || !currentShape) return;
      const pos = getPos(e);

      setCurrentShape((prev) => {
        if (!prev) return prev;
        if (prev.tool === 'pen') {
          return { ...prev, points: [...(prev.points ?? []), pos.x, pos.y] };
        }
        if (prev.tool === 'line' || prev.tool === 'arrow') {
          const pts = [...(prev.points ?? [])];
          pts[2] = pos.x;
          pts[3] = pos.y;
          return { ...prev, points: pts };
        }
        if (prev.tool === 'rect') {
          return {
            ...prev,
            width: pos.x - (prev.x ?? 0),
            height: pos.y - (prev.y ?? 0),
          };
        }
        if (prev.tool === 'circle') {
          const dx = pos.x - (prev.x ?? 0);
          const dy = pos.y - (prev.y ?? 0);
          return { ...prev, radius: Math.sqrt(dx * dx + dy * dy) };
        }
        return prev;
      });
    },
    [drawing, currentShape, getPos]
  );

  const handleMouseUp = useCallback(() => {
    if (!drawing || !currentShape) return;
    setShapes((prev) => [...prev, currentShape]);
    setCurrentShape(null);
    setDrawing(false);
  }, [drawing, currentShape]);

  const handleUndo = useCallback(() => {
    setShapes((prev) => prev.slice(0, -1));
  }, []);

  const handleDone = useCallback(() => {
    onDone(shapes);
  }, [shapes, onDone]);

  const renderShape = useCallback((shape: AnnotationShape) => {
    const common = {
      key: shape.id,
      stroke: shape.color,
      strokeWidth: shape.lineWidth,
      listening: false,
    };
    if (shape.tool === 'pen') {
      return <Line {...common} points={shape.points} tension={0.4} lineCap="round" lineJoin="round" />;
    }
    if (shape.tool === 'line') {
      return <Line {...common} points={shape.points} lineCap="round" />;
    }
    if (shape.tool === 'arrow') {
      return <Arrow {...common} points={shape.points} pointerLength={10} pointerWidth={8} fill={shape.color} />;
    }
    if (shape.tool === 'rect') {
      return <Rect {...common} x={shape.x} y={shape.y} width={shape.width} height={shape.height} fill="transparent" />;
    }
    if (shape.tool === 'circle') {
      return <Circle {...common} x={shape.x} y={shape.y} radius={shape.radius} fill="transparent" />;
    }
    if (shape.tool === 'text') {
      return <Text key={shape.id} x={shape.x} y={shape.y} text={shape.text} fill={shape.color} fontSize={16} listening={false} />;
    }
    return null;
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onCancel} />

      <div className="relative bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-sm font-semibold text-gray-100">Annotate</h2>
          <button onClick={onCancel} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-4 px-5 py-2 border-b border-gray-800 flex-wrap">
          {/* Tools */}
          <div className="flex items-center gap-1">
            {(Object.keys(TOOL_ICONS) as DrawTool[]).map((t) => (
              <button
                key={t}
                onClick={() => setTool(t)}
                className={[
                  'px-2 py-1 rounded-lg text-xs transition-colors',
                  tool === t ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200',
                ].join(' ')}
              >
                {TOOL_ICONS[t]}
              </button>
            ))}
          </div>

          {/* Colors */}
          <div className="flex items-center gap-1">
            {PRESET_COLORS.map((c) => (
              <button
                key={c}
                onClick={() => setColor(c)}
                className="w-5 h-5 rounded-full border-2 transition-all"
                style={{
                  background: c,
                  borderColor: color === c ? 'white' : 'transparent',
                }}
              />
            ))}
            <input
              type="color"
              value={customColor}
              onChange={(e) => { setCustomColor(e.target.value); setColor(e.target.value); }}
              className="w-5 h-5 rounded cursor-pointer border-0 bg-transparent"
              title="Custom color"
            />
          </div>

          {/* Line width */}
          <div className="flex items-center gap-1">
            {(Object.keys(LINE_WIDTHS) as LineWidth[]).map((w) => (
              <button
                key={w}
                onClick={() => setLineWidthKey(w)}
                className={[
                  'px-2 py-1 rounded-lg text-xs transition-colors',
                  lineWidthKey === w ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200',
                ].join(' ')}
              >
                {w}
              </button>
            ))}
          </div>

          {/* Undo */}
          <button
            onClick={handleUndo}
            disabled={shapes.length === 0}
            className="flex items-center gap-1 px-2 py-1 rounded-lg bg-gray-800 text-gray-400 hover:text-gray-200 text-xs transition-colors disabled:opacity-40"
          >
            <Undo2 size={12} /> Undo
          </button>
        </div>

        {/* Konva canvas */}
        <div className="relative m-4 rounded-xl overflow-hidden bg-gray-950">
          <img
            src={imageUrl}
            alt="Annotate"
            className="absolute inset-0 w-full h-full object-contain pointer-events-none"
            draggable={false}
          />
          <Stage
            ref={stageRef}
            width={CANVAS_W}
            height={CANVAS_H}
            style={{ cursor: 'crosshair' }}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
          >
            <Layer>
              {shapes.map(renderShape)}
              {currentShape && renderShape(currentShape)}
            </Layer>
          </Stage>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-3 border-t border-gray-700">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors">
            Cancel
          </button>
          <button
            onClick={handleDone}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
});

export default AnnotateTool;
