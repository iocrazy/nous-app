import { useState, useCallback, useRef, useEffect, type PointerEvent as ReactPointerEvent } from 'react';
import { ArrowRight, Check, Circle, PenLine, Square, Trash2, Type, Undo2, X } from 'lucide-react';
import { UiButton } from '../../../components/ui';
import { loadImageElement, canvasToDataUrl } from '../application/imageData';

interface AnnotateToolEditorProps {
  imageUrl: string;
  onConfirm: (resultUrl: string) => void;
  onCancel: () => void;
}

type AnnotationToolType = 'pen' | 'rect' | 'ellipse' | 'arrow' | 'text';

interface AnnotationItem {
  id: string;
  type: AnnotationToolType;
  color: string;
  lineWidth: number;
  points?: number[];
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  text?: string;
  fontSize?: number;
}

const TOOL_BUTTONS: Array<{ type: AnnotationToolType; label: string; icon: typeof Square }> = [
  { type: 'pen', label: 'Pen', icon: PenLine },
  { type: 'rect', label: 'Rectangle', icon: Square },
  { type: 'ellipse', label: 'Ellipse', icon: Circle },
  { type: 'arrow', label: 'Arrow', icon: ArrowRight },
  { type: 'text', label: 'Text', icon: Type },
];

const STROKE_WIDTHS = [2, 4, 6, 8];
const PRESET_COLORS = ['#FF4444', '#FF8800', '#FFDD00', '#44CC44', '#4488FF', '#FFFFFF', '#000000'];

function createAnnotationId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function AnnotateToolEditor({ imageUrl, onConfirm, onCancel }: AnnotateToolEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeTool, setActiveTool] = useState<AnnotationToolType>('pen');
  const [color, setColor] = useState('#FF4444');
  const [lineWidth, setLineWidth] = useState(4);
  const [annotations, setAnnotations] = useState<AnnotationItem[]>([]);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imageEl, setImageEl] = useState<HTMLImageElement | null>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [textInput, setTextInput] = useState<{ x: number; y: number; value: string } | null>(null);

  const drawingRef = useRef<{
    active: boolean;
    points: number[];
    startX: number;
    startY: number;
    currentX: number;
    currentY: number;
  } | null>(null);

  // Load image
  useEffect(() => {
    void loadImageElement(imageUrl)
      .then((img) => {
        setImageEl(img);
        const maxWidth = 480;
        const scale = Math.min(1, maxWidth / img.naturalWidth);
        setCanvasSize({
          width: Math.round(img.naturalWidth * scale),
          height: Math.round(img.naturalHeight * scale),
        });
      })
      .catch(() => setError('Failed to load image'));
  }, [imageUrl]);

  // Redraw canvas
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || !imageEl) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(imageEl, 0, 0, canvas.width, canvas.height);

    for (const item of annotations) {
      ctx.strokeStyle = item.color;
      ctx.fillStyle = item.color;
      ctx.lineWidth = item.lineWidth;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';

      if (item.type === 'pen' && item.points && item.points.length >= 4) {
        ctx.beginPath();
        ctx.moveTo(item.points[0], item.points[1]);
        for (let i = 2; i < item.points.length; i += 2) {
          ctx.lineTo(item.points[i], item.points[i + 1]);
        }
        ctx.stroke();
      } else if (item.type === 'rect' && item.width != null && item.height != null) {
        ctx.strokeRect(item.x ?? 0, item.y ?? 0, item.width, item.height);
      } else if (item.type === 'ellipse' && item.width != null && item.height != null) {
        const cx = (item.x ?? 0) + item.width / 2;
        const cy = (item.y ?? 0) + item.height / 2;
        ctx.beginPath();
        ctx.ellipse(cx, cy, Math.abs(item.width / 2), Math.abs(item.height / 2), 0, 0, Math.PI * 2);
        ctx.stroke();
      } else if (item.type === 'arrow' && item.x != null && item.y != null && item.width != null && item.height != null) {
        const x1 = item.x; const y1 = item.y;
        const x2 = item.x + item.width; const y2 = item.y + item.height;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        // Arrowhead
        const angle = Math.atan2(y2 - y1, x2 - x1);
        const headLen = Math.max(10, item.lineWidth * 3);
        ctx.beginPath();
        ctx.moveTo(x2, y2);
        ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6));
        ctx.moveTo(x2, y2);
        ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6));
        ctx.stroke();
      } else if (item.type === 'text' && item.text) {
        ctx.font = `${item.fontSize ?? 16}px sans-serif`;
        ctx.fillText(item.text, item.x ?? 0, item.y ?? 0);
      }
    }
  }, [annotations, imageEl]);

  useEffect(() => { redraw(); }, [redraw]);

  const getCanvasCoords = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left) * (canvas.width / rect.width),
      y: (e.clientY - rect.top) * (canvas.height / rect.height),
    };
  };

  const handlePointerDown = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    const { x, y } = getCanvasCoords(e);
    if (activeTool === 'text') {
      setTextInput({ x, y, value: '' });
      return;
    }
    drawingRef.current = { active: true, points: [x, y], startX: x, startY: y, currentX: x, currentY: y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    const d = drawingRef.current;
    if (!d || !d.active) return;
    const { x, y } = getCanvasCoords(e);
    d.currentX = x; d.currentY = y;
    if (activeTool === 'pen') {
      d.points.push(x, y);
    }
    // Live preview
    redraw();
    const ctx = canvasRef.current?.getContext('2d');
    if (!ctx) return;
    ctx.strokeStyle = color; ctx.lineWidth = lineWidth;
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';

    if (activeTool === 'pen') {
      ctx.beginPath();
      ctx.moveTo(d.points[0], d.points[1]);
      for (let i = 2; i < d.points.length; i += 2) ctx.lineTo(d.points[i], d.points[i + 1]);
      ctx.stroke();
    } else if (activeTool === 'rect') {
      ctx.strokeRect(d.startX, d.startY, x - d.startX, y - d.startY);
    } else if (activeTool === 'ellipse') {
      const cx = (d.startX + x) / 2; const cy = (d.startY + y) / 2;
      ctx.beginPath();
      ctx.ellipse(cx, cy, Math.abs(x - d.startX) / 2, Math.abs(y - d.startY) / 2, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else if (activeTool === 'arrow') {
      ctx.beginPath(); ctx.moveTo(d.startX, d.startY); ctx.lineTo(x, y); ctx.stroke();
      const angle = Math.atan2(y - d.startY, x - d.startX);
      const headLen = Math.max(10, lineWidth * 3);
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x - headLen * Math.cos(angle - Math.PI / 6), y - headLen * Math.sin(angle - Math.PI / 6));
      ctx.moveTo(x, y);
      ctx.lineTo(x - headLen * Math.cos(angle + Math.PI / 6), y - headLen * Math.sin(angle + Math.PI / 6));
      ctx.stroke();
    }
  };

  const handlePointerUp = () => {
    const d = drawingRef.current;
    if (!d || !d.active) return;
    drawingRef.current = null;

    const item: AnnotationItem = {
      id: createAnnotationId(),
      type: activeTool,
      color,
      lineWidth,
    };

    if (activeTool === 'pen') {
      item.points = [...d.points];
    } else {
      item.x = d.startX; item.y = d.startY;
      item.width = d.currentX - d.startX;
      item.height = d.currentY - d.startY;
    }

    setAnnotations((prev) => [...prev, item]);
  };

  const handleTextSubmit = () => {
    if (!textInput || !textInput.value.trim()) { setTextInput(null); return; }
    setAnnotations((prev) => [...prev, {
      id: createAnnotationId(),
      type: 'text',
      color,
      lineWidth,
      x: textInput.x,
      y: textInput.y,
      text: textInput.value,
      fontSize: Math.max(12, Math.round(canvasSize.height * 0.04)),
    }]);
    setTextInput(null);
  };

  const handleUndo = () => { setAnnotations((prev) => prev.slice(0, -1)); };
  const handleClear = () => { setAnnotations([]); };

  const handleConfirm = useCallback(async () => {
    setProcessing(true); setError(null);
    try {
      if (!imageEl) throw new Error('Image not loaded');
      // Render at full resolution
      const canvas = document.createElement('canvas');
      canvas.width = imageEl.naturalWidth;
      canvas.height = imageEl.naturalHeight;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('Canvas context failed');
      ctx.drawImage(imageEl, 0, 0);
      const scaleX = imageEl.naturalWidth / canvasSize.width;
      const scaleY = imageEl.naturalHeight / canvasSize.height;

      for (const item of annotations) {
        ctx.strokeStyle = item.color; ctx.fillStyle = item.color;
        ctx.lineWidth = item.lineWidth * Math.max(scaleX, scaleY);
        ctx.lineCap = 'round'; ctx.lineJoin = 'round';

        if (item.type === 'pen' && item.points) {
          ctx.beginPath();
          ctx.moveTo(item.points[0] * scaleX, item.points[1] * scaleY);
          for (let i = 2; i < item.points.length; i += 2) ctx.lineTo(item.points[i] * scaleX, item.points[i + 1] * scaleY);
          ctx.stroke();
        } else if (item.type === 'rect' && item.width != null) {
          ctx.strokeRect((item.x ?? 0) * scaleX, (item.y ?? 0) * scaleY, item.width * scaleX, (item.height ?? 0) * scaleY);
        } else if (item.type === 'ellipse' && item.width != null) {
          const cx = ((item.x ?? 0) + item.width / 2) * scaleX;
          const cy = ((item.y ?? 0) + (item.height ?? 0) / 2) * scaleY;
          ctx.beginPath();
          ctx.ellipse(cx, cy, Math.abs(item.width / 2) * scaleX, Math.abs((item.height ?? 0) / 2) * scaleY, 0, 0, Math.PI * 2);
          ctx.stroke();
        } else if (item.type === 'arrow' && item.width != null) {
          const x1 = (item.x ?? 0) * scaleX; const y1 = (item.y ?? 0) * scaleY;
          const x2 = x1 + item.width * scaleX; const y2 = y1 + (item.height ?? 0) * scaleY;
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
          const angle = Math.atan2(y2 - y1, x2 - x1);
          const headLen = Math.max(10, ctx.lineWidth * 3);
          ctx.beginPath();
          ctx.moveTo(x2, y2);
          ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6));
          ctx.moveTo(x2, y2);
          ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6));
          ctx.stroke();
        } else if (item.type === 'text' && item.text) {
          const fontSize = (item.fontSize ?? 16) * Math.max(scaleX, scaleY);
          ctx.font = `${fontSize}px sans-serif`;
          ctx.fillText(item.text, (item.x ?? 0) * scaleX, (item.y ?? 0) * scaleY);
        }
      }

      onConfirm(canvasToDataUrl(canvas));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Annotation failed');
    } finally {
      setProcessing(false);
    }
  }, [annotations, canvasSize, imageEl, onConfirm]);

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-text-dark">
        <PenLine className="h-4 w-4" />
        <span>Annotate Image</span>
      </div>

      {/* Tool bar */}
      <div className="flex items-center gap-1.5">
        {TOOL_BUTTONS.map((tool) => {
          const Icon = tool.icon;
          return (
            <button key={tool.type} type="button" onClick={() => setActiveTool(tool.type)}
              className={`flex items-center gap-1 rounded-full px-2 py-1 text-[11px] transition-colors ${
                activeTool === tool.type
                  ? 'bg-indigo-600 text-white'
                  : 'bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]'
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {tool.label}
            </button>
          );
        })}
        <div className="ml-auto flex items-center gap-1">
          <button type="button" onClick={handleUndo} disabled={annotations.length === 0}
            className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:bg-[rgba(255,255,255,0.1)] disabled:opacity-30"
          ><Undo2 className="h-3.5 w-3.5" /></button>
          <button type="button" onClick={handleClear} disabled={annotations.length === 0}
            className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:bg-[rgba(255,255,255,0.1)] disabled:opacity-30"
          ><Trash2 className="h-3.5 w-3.5" /></button>
        </div>
      </div>

      {/* Color + stroke width */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1">
          {PRESET_COLORS.map((c) => (
            <button key={c} type="button" onClick={() => setColor(c)}
              className={`h-5 w-5 rounded-full border-2 transition-transform ${color === c ? 'border-white scale-110' : 'border-transparent'}`}
              style={{ backgroundColor: c }}
            />
          ))}
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)}
            className="ml-1 h-5 w-5 cursor-pointer rounded border-none bg-transparent"
          />
        </div>
        <div className="flex items-center gap-1">
          {STROKE_WIDTHS.map((w) => (
            <button key={w} type="button" onClick={() => setLineWidth(w)}
              className={`flex h-6 w-6 items-center justify-center rounded text-[10px] ${lineWidth === w ? 'bg-indigo-600 text-white' : 'bg-[rgba(255,255,255,0.08)] text-text-muted'}`}
            >{w}</button>
          ))}
        </div>
      </div>

      {/* Canvas */}
      <div ref={containerRef} className="relative overflow-hidden rounded-lg border border-[rgba(255,255,255,0.1)] bg-bg-dark/60">
        <canvas
          ref={canvasRef}
          width={canvasSize.width}
          height={canvasSize.height}
          className="block w-full cursor-crosshair"
          style={{ aspectRatio: canvasSize.width > 0 ? `${canvasSize.width} / ${canvasSize.height}` : undefined }}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
        />
        {textInput && (
          <input
            type="text"
            autoFocus
            value={textInput.value}
            onChange={(e) => setTextInput((prev) => prev ? { ...prev, value: e.target.value } : null)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleTextSubmit(); if (e.key === 'Escape') setTextInput(null); }}
            onBlur={handleTextSubmit}
            className="absolute z-10 rounded border border-indigo-400 bg-black/80 px-1.5 py-0.5 text-sm text-white outline-none"
            style={{
              left: `${(textInput.x / canvasSize.width) * 100}%`,
              top: `${(textInput.y / canvasSize.height) * 100}%`,
              minWidth: 80,
            }}
          />
        )}
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="flex justify-end gap-2">
        <UiButton size="sm" variant="ghost" onClick={onCancel}>
          <X className="h-3.5 w-3.5" /> Cancel
        </UiButton>
        <UiButton size="sm" variant="primary" disabled={processing || annotations.length === 0} onClick={handleConfirm}>
          <Check className="h-3.5 w-3.5" /> {processing ? 'Processing...' : 'Apply'}
        </UiButton>
      </div>
    </div>
  );
}
