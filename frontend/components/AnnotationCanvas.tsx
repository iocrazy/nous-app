import React, { useRef, useState, useEffect, useCallback } from 'react';
import { DrawingData, Stroke } from '../types';

interface AnnotationCanvasProps {
  width: number;
  height: number;
  isActive: boolean;
  tool: 'pen' | 'arrow' | 'rect' | 'circle' | 'text';
  color: string;
  strokeWidth: number;
  existingDrawing?: DrawingData | null;
  onDrawingChange: (data: DrawingData) => void;
  onClose: () => void;
}

function generateId(): string {
  return `s_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

function drawArrowhead(
  ctx: CanvasRenderingContext2D,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  headLength: number,
) {
  const angle = Math.atan2(toY - fromY, toX - fromX);
  ctx.beginPath();
  ctx.moveTo(toX, toY);
  ctx.lineTo(
    toX - headLength * Math.cos(angle - Math.PI / 6),
    toY - headLength * Math.sin(angle - Math.PI / 6),
  );
  ctx.moveTo(toX, toY);
  ctx.lineTo(
    toX - headLength * Math.cos(angle + Math.PI / 6),
    toY - headLength * Math.sin(angle + Math.PI / 6),
  );
  ctx.stroke();
}

export const AnnotationCanvas: React.FC<AnnotationCanvasProps> = ({
  width,
  height,
  isActive,
  tool,
  color,
  strokeWidth: lineWidth,
  existingDrawing,
  onDrawingChange,
  onClose,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const [currentStroke, setCurrentStroke] = useState<Stroke | null>(null);
  const [isDrawing, setIsDrawing] = useState(false);
  const [textInput, setTextInput] = useState<{ x: number; y: number; visible: boolean }>({
    x: 0,
    y: 0,
    visible: false,
  });
  const [textValue, setTextValue] = useState('');
  const textInputRef = useRef<HTMLInputElement>(null);

  // Load existing drawing on mount
  useEffect(() => {
    if (existingDrawing && existingDrawing.strokes.length > 0) {
      setStrokes(existingDrawing.strokes);
    }
  }, [existingDrawing]);

  // Re-render canvas whenever strokes or currentStroke changes
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.clearRect(0, 0, width, height);

    const allStrokes = currentStroke ? [...strokes, currentStroke] : strokes;

    allStrokes.forEach((stroke) => {
      ctx.strokeStyle = stroke.color;
      ctx.fillStyle = stroke.color;
      ctx.lineWidth = stroke.strokeWidth;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';

      switch (stroke.tool) {
        case 'pen': {
          if (stroke.points.length < 2) break;
          ctx.beginPath();
          stroke.points.forEach((p, i) => {
            if (i === 0) ctx.moveTo(p.x, p.y);
            else ctx.lineTo(p.x, p.y);
          });
          ctx.stroke();
          break;
        }
        case 'arrow': {
          if (stroke.points.length < 2) break;
          const start = stroke.points[0];
          const end = stroke.points[stroke.points.length - 1];
          ctx.beginPath();
          ctx.moveTo(start.x, start.y);
          ctx.lineTo(end.x, end.y);
          ctx.stroke();
          const headLen = Math.max(10, stroke.strokeWidth * 4);
          drawArrowhead(ctx, start.x, start.y, end.x, end.y, headLen);
          break;
        }
        case 'rect': {
          if (stroke.points.length < 2) break;
          const s = stroke.points[0];
          const e = stroke.points[stroke.points.length - 1];
          ctx.strokeRect(s.x, s.y, e.x - s.x, e.y - s.y);
          break;
        }
        case 'circle': {
          if (stroke.points.length < 2) break;
          const p0 = stroke.points[0];
          const p1 = stroke.points[stroke.points.length - 1];
          const cx = (p0.x + p1.x) / 2;
          const cy = (p0.y + p1.y) / 2;
          const rx = Math.abs(p1.x - p0.x) / 2;
          const ry = Math.abs(p1.y - p0.y) / 2;
          ctx.beginPath();
          ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
          ctx.stroke();
          break;
        }
        case 'text': {
          if (stroke.text && stroke.points.length > 0) {
            const pt = stroke.points[0];
            const fontSize = Math.max(14, stroke.strokeWidth * 5);
            ctx.font = `${fontSize}px sans-serif`;
            ctx.fillText(stroke.text, pt.x, pt.y);
          }
          break;
        }
      }
    });
  }, [strokes, currentStroke, width, height]);

  useEffect(() => {
    draw();
  }, [draw]);

  // Emit drawing change whenever strokes update
  useEffect(() => {
    onDrawingChange({ strokes, width, height });
  }, [strokes, width, height, onDrawingChange]);

  // Focus text input when it becomes visible
  useEffect(() => {
    if (textInput.visible && textInputRef.current) {
      textInputRef.current.focus();
    }
  }, [textInput.visible]);

  const getCanvasPoint = (e: React.MouseEvent<HTMLCanvasElement>): { x: number; y: number } => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) / rect.width) * width,
      y: ((e.clientY - rect.top) / rect.height) * height,
    };
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isActive) return;
    e.preventDefault();
    e.stopPropagation();

    const point = getCanvasPoint(e);

    if (tool === 'text') {
      setTextInput({ x: point.x, y: point.y, visible: true });
      setTextValue('');
      return;
    }

    const newStroke: Stroke = {
      id: generateId(),
      tool,
      points: [point],
      color,
      strokeWidth: lineWidth,
    };

    setCurrentStroke(newStroke);
    setIsDrawing(true);
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawing || !currentStroke || !isActive) return;
    e.preventDefault();
    e.stopPropagation();

    const point = getCanvasPoint(e);

    if (tool === 'pen') {
      setCurrentStroke((prev) =>
        prev ? { ...prev, points: [...prev.points, point] } : null,
      );
    } else {
      // For shape tools, keep only start and current point
      setCurrentStroke((prev) =>
        prev ? { ...prev, points: [prev.points[0], point] } : null,
      );
    }
  };

  const handleMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawing || !currentStroke || !isActive) return;
    e.preventDefault();
    e.stopPropagation();

    // Only add stroke if it has meaningful content
    if (currentStroke.points.length >= 2) {
      setStrokes((prev) => [...prev, currentStroke]);
    }
    setCurrentStroke(null);
    setIsDrawing(false);
  };

  const handleMouseLeave = () => {
    if (isDrawing && currentStroke) {
      if (currentStroke.points.length >= 2) {
        setStrokes((prev) => [...prev, currentStroke]);
      }
      setCurrentStroke(null);
      setIsDrawing(false);
    }
  };

  const handleTextSubmit = () => {
    if (textValue.trim()) {
      const newStroke: Stroke = {
        id: generateId(),
        tool: 'text',
        points: [{ x: textInput.x, y: textInput.y }],
        color,
        strokeWidth: lineWidth,
        text: textValue.trim(),
      };
      setStrokes((prev) => [...prev, newStroke]);
    }
    setTextInput({ x: 0, y: 0, visible: false });
    setTextValue('');
  };

  const handleTextKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleTextSubmit();
    } else if (e.key === 'Escape') {
      setTextInput({ x: 0, y: 0, visible: false });
      setTextValue('');
    }
  };

  // Expose undo/clear methods via public API through strokes state
  // These are called by parent through prop callbacks in AnnotationToolbar
  // We use imperative handle pattern instead
  const undo = useCallback(() => {
    setStrokes((prev) => prev.slice(0, -1));
  }, []);

  const clear = useCallback(() => {
    setStrokes([]);
  }, []);

  // Expose undo/clear to parent via ref-like mechanism
  // We attach them to the canvas element as custom properties
  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas) {
      (canvas as any).__undo = undo;
      (canvas as any).__clear = clear;
    }
  }, [undo, clear]);

  // Calculate text input position in screen coordinates
  const getTextInputStyle = (): React.CSSProperties => {
    const canvas = canvasRef.current;
    if (!canvas) return { display: 'none' };
    const rect = canvas.getBoundingClientRect();
    const scaleX = rect.width / width;
    const scaleY = rect.height / height;
    return {
      position: 'absolute',
      left: `${textInput.x * scaleX}px`,
      top: `${textInput.y * scaleY}px`,
      transform: 'translate(0, -100%)',
    };
  };

  return (
    <div
      className="absolute inset-0 z-10"
      style={{ pointerEvents: isActive ? 'auto' : 'none' }}
    >
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className="absolute inset-0 w-full h-full"
        style={{
          cursor: isActive
            ? tool === 'text'
              ? 'text'
              : 'crosshair'
            : 'default',
        }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
      />

      {/* Text input overlay */}
      {textInput.visible && (
        <div style={getTextInputStyle()}>
          <input
            ref={textInputRef}
            type="text"
            value={textValue}
            onChange={(e) => setTextValue(e.target.value)}
            onKeyDown={handleTextKeyDown}
            onBlur={handleTextSubmit}
            className="bg-zinc-900/90 border border-zinc-600 text-white text-sm px-2 py-1 rounded outline-none min-w-[120px]"
            placeholder="Type text..."
          />
        </div>
      )}
    </div>
  );
};

export default AnnotationCanvas;
