import { useState, useCallback, useRef, useEffect, useMemo, type PointerEvent as ReactPointerEvent } from 'react';
import { ArrowRight, Check, Circle, Eraser, MousePointer2, PenLine, Square, Trash2, Type, Undo2, Redo2, X } from 'lucide-react';
import { UiButton } from '../../../components/ui';
import { loadImageElement, canvasToDataUrl } from '../application/imageData';

interface AnnotateToolEditorProps {
  imageUrl: string;
  onConfirm: (resultUrl: string) => void;
  onCancel: () => void;
}

type AnnotationToolType = 'select' | 'pen' | 'rect' | 'ellipse' | 'arrow' | 'text' | 'eraser';

interface AnnotationItem {
  id: string;
  type: Exclude<AnnotationToolType, 'select' | 'eraser'>;
  color: string;
  lineWidth: number;
  opacity: number;
  points?: number[];
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  text?: string;
  fontSize?: number;
}

const TOOL_BUTTONS: Array<{ type: AnnotationToolType; label: string; icon: typeof Square; group: 'select' | 'draw' | 'shape' | 'other' }> = [
  { type: 'select', label: 'Select', icon: MousePointer2, group: 'select' },
  { type: 'pen', label: 'Pen', icon: PenLine, group: 'draw' },
  { type: 'eraser', label: 'Eraser', icon: Eraser, group: 'draw' },
  { type: 'rect', label: 'Rectangle', icon: Square, group: 'shape' },
  { type: 'ellipse', label: 'Ellipse', icon: Circle, group: 'shape' },
  { type: 'arrow', label: 'Arrow', icon: ArrowRight, group: 'shape' },
  { type: 'text', label: 'Text', icon: Type, group: 'other' },
];

const STROKE_WIDTHS = [2, 4, 6, 8];
const FONT_SIZES = [12, 16, 20, 24, 32, 48];
const PRESET_COLORS = ['#FF4444', '#FF8800', '#FFDD00', '#44CC44', '#4488FF', '#FFFFFF', '#000000'];
const MAX_UNDO_STACK = 40;

function createAnnotationId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function distanceToLine(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return Math.hypot(px - x1, py - y1);
  const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / lengthSq));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function hitTestAnnotation(item: AnnotationItem, x: number, y: number, threshold: number): boolean {
  const effectiveThreshold = Math.max(threshold, item.lineWidth + 4);

  if (item.type === 'pen' && item.points && item.points.length >= 4) {
    for (let i = 0; i < item.points.length - 2; i += 2) {
      if (distanceToLine(x, y, item.points[i], item.points[i + 1], item.points[i + 2], item.points[i + 3]) < effectiveThreshold) {
        return true;
      }
    }
    return false;
  }

  if (item.type === 'rect' && item.width != null && item.height != null) {
    const ix = item.x ?? 0;
    const iy = item.y ?? 0;
    const w = item.width;
    const h = item.height;
    const minX = Math.min(ix, ix + w);
    const maxX = Math.max(ix, ix + w);
    const minY = Math.min(iy, iy + h);
    const maxY = Math.max(iy, iy + h);
    return x >= minX - effectiveThreshold && x <= maxX + effectiveThreshold &&
           y >= minY - effectiveThreshold && y <= maxY + effectiveThreshold;
  }

  if (item.type === 'ellipse' && item.width != null && item.height != null) {
    const cx = (item.x ?? 0) + item.width / 2;
    const cy = (item.y ?? 0) + item.height / 2;
    const rx = Math.abs(item.width / 2) + effectiveThreshold;
    const ry = Math.abs(item.height / 2) + effectiveThreshold;
    if (rx === 0 || ry === 0) return false;
    return ((x - cx) * (x - cx)) / (rx * rx) + ((y - cy) * (y - cy)) / (ry * ry) <= 1;
  }

  if (item.type === 'arrow' && item.width != null && item.height != null) {
    const x1 = item.x ?? 0;
    const y1 = item.y ?? 0;
    return distanceToLine(x, y, x1, y1, x1 + item.width, y1 + item.height) < effectiveThreshold;
  }

  if (item.type === 'text' && item.text) {
    const ix = item.x ?? 0;
    const iy = item.y ?? 0;
    const fontSize = item.fontSize ?? 16;
    const estimatedWidth = item.text.length * fontSize * 0.6;
    return x >= ix - 4 && x <= ix + estimatedWidth + 4 && y >= iy - fontSize && y <= iy + 4;
  }

  return false;
}

export function AnnotateToolEditor({ imageUrl, onConfirm, onCancel }: AnnotateToolEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeTool, setActiveTool] = useState<AnnotationToolType>('pen');
  const [color, setColor] = useState('#FF4444');
  const [lineWidth, setLineWidth] = useState(4);
  const [fontSize, setFontSize] = useState(20);
  const [opacity, setOpacity] = useState(1);
  const [annotations, setAnnotations] = useState<AnnotationItem[]>([]);
  const [undoStack, setUndoStack] = useState<AnnotationItem[][]>([]);
  const [redoStack, setRedoStack] = useState<AnnotationItem[][]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imageEl, setImageEl] = useState<HTMLImageElement | null>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [textInput, setTextInput] = useState<{ x: number; y: number; value: string } | null>(null);
  const [dragState, setDragState] = useState<{ id: string; startX: number; startY: number; origItem: AnnotationItem } | null>(null);

  const drawingRef = useRef<{
    active: boolean;
    points: number[];
    startX: number;
    startY: number;
    currentX: number;
    currentY: number;
  } | null>(null);

  const canUndo = undoStack.length > 0;
  const canRedo = redoStack.length > 0;
  const selectedAnnotation = useMemo(
    () => annotations.find((item) => item.id === selectedId) ?? null,
    [annotations, selectedId]
  );

  // Save state for undo
  const pushUndo = useCallback((current: AnnotationItem[]) => {
    setUndoStack((prev) => [...prev, current].slice(-MAX_UNDO_STACK));
    setRedoStack([]);
  }, []);

  // Load image
  useEffect(() => {
    void loadImageElement(imageUrl)
      .then((img) => {
        setImageEl(img);
        const maxWidth = 560;
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
      ctx.save();
      ctx.globalAlpha = item.opacity ?? 1;
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
        const angle = Math.atan2(y2 - y1, x2 - x1);
        const headLen = Math.max(10, item.lineWidth * 3);
        ctx.beginPath();
        ctx.moveTo(x2, y2);
        ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6));
        ctx.moveTo(x2, y2);
        ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6));
        ctx.stroke();
      } else if (item.type === 'text' && item.text) {
        ctx.font = `bold ${item.fontSize ?? 16}px sans-serif`;
        ctx.fillText(item.text, item.x ?? 0, item.y ?? 0);
      }

      // Selection highlight
      if (item.id === selectedId) {
        ctx.strokeStyle = '#6366f1';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 3]);
        if (item.type === 'rect' && item.width != null && item.height != null) {
          ctx.strokeRect((item.x ?? 0) - 3, (item.y ?? 0) - 3, item.width + 6, item.height + 6);
        } else if (item.type === 'ellipse' && item.width != null && item.height != null) {
          const cx = (item.x ?? 0) + item.width / 2;
          const cy = (item.y ?? 0) + item.height / 2;
          ctx.beginPath();
          ctx.ellipse(cx, cy, Math.abs(item.width / 2) + 3, Math.abs(item.height / 2) + 3, 0, 0, Math.PI * 2);
          ctx.stroke();
        } else if (item.type === 'text' && item.text) {
          const tw = (item.fontSize ?? 16) * item.text.length * 0.6;
          ctx.strokeRect((item.x ?? 0) - 3, (item.y ?? 0) - (item.fontSize ?? 16) - 3, tw + 6, (item.fontSize ?? 16) + 6);
        }
        ctx.setLineDash([]);
      }

      ctx.restore();
    }
  }, [annotations, imageEl, selectedId]);

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

    if (activeTool === 'select') {
      // Try to select an annotation under pointer
      const hitItem = [...annotations].reverse().find((item) => hitTestAnnotation(item, x, y, 8));
      if (hitItem) {
        setSelectedId(hitItem.id);
        setDragState({ id: hitItem.id, startX: x, startY: y, origItem: { ...hitItem } });
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
      } else {
        setSelectedId(null);
      }
      return;
    }

    if (activeTool === 'eraser') {
      // Erase annotation under pointer
      const hitItem = [...annotations].reverse().find((item) => hitTestAnnotation(item, x, y, 12));
      if (hitItem) {
        pushUndo(annotations);
        setAnnotations((prev) => prev.filter((item) => item.id !== hitItem.id));
        if (selectedId === hitItem.id) setSelectedId(null);
      }
      return;
    }

    drawingRef.current = { active: true, points: [x, y], startX: x, startY: y, currentX: x, currentY: y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    // Handle select drag (move annotation)
    if (dragState) {
      const { x, y } = getCanvasCoords(e);
      const dx = x - dragState.startX;
      const dy = y - dragState.startY;
      setAnnotations((prev) =>
        prev.map((item) => {
          if (item.id !== dragState.id) return item;
          const orig = dragState.origItem;
          if (orig.type === 'pen' && orig.points) {
            return {
              ...item,
              points: orig.points.map((p, i) => (i % 2 === 0 ? p + dx : p + dy)),
            };
          }
          return { ...item, x: (orig.x ?? 0) + dx, y: (orig.y ?? 0) + dy };
        })
      );
      return;
    }

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
    ctx.save();
    ctx.globalAlpha = opacity;
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
    ctx.restore();
  };

  const handlePointerUp = () => {
    // Finalize select drag
    if (dragState) {
      pushUndo(annotations.map((item) => (item.id === dragState.id ? dragState.origItem : item)));
      setDragState(null);
      return;
    }

    const d = drawingRef.current;
    if (!d || !d.active) return;
    drawingRef.current = null;

    const drawTool = activeTool as Exclude<AnnotationToolType, 'select' | 'eraser'>;
    const item: AnnotationItem = {
      id: createAnnotationId(),
      type: drawTool,
      color,
      lineWidth,
      opacity,
    };

    if (activeTool === 'pen') {
      if (d.points.length < 4) return;
      item.points = [...d.points];
    } else {
      item.x = d.startX; item.y = d.startY;
      item.width = d.currentX - d.startX;
      item.height = d.currentY - d.startY;
    }

    pushUndo(annotations);
    setAnnotations((prev) => [...prev, item]);
  };

  const handleTextSubmit = () => {
    if (!textInput || !textInput.value.trim()) { setTextInput(null); return; }
    pushUndo(annotations);
    setAnnotations((prev) => [...prev, {
      id: createAnnotationId(),
      type: 'text' as const,
      color,
      lineWidth,
      opacity,
      x: textInput.x,
      y: textInput.y,
      text: textInput.value,
      fontSize,
    }]);
    setTextInput(null);
  };

  const handleUndo = useCallback(() => {
    if (undoStack.length === 0) return;
    const previous = undoStack[undoStack.length - 1];
    setRedoStack((prev) => [...prev, annotations].slice(-MAX_UNDO_STACK));
    setUndoStack((prev) => prev.slice(0, -1));
    setAnnotations(previous);
    setSelectedId(null);
  }, [annotations, undoStack]);

  const handleRedo = useCallback(() => {
    if (redoStack.length === 0) return;
    const next = redoStack[redoStack.length - 1];
    setUndoStack((prev) => [...prev, annotations].slice(-MAX_UNDO_STACK));
    setRedoStack((prev) => prev.slice(0, -1));
    setAnnotations(next);
    setSelectedId(null);
  }, [annotations, redoStack]);

  const handleDeleteSelected = useCallback(() => {
    if (!selectedId) return;
    pushUndo(annotations);
    setAnnotations((prev) => prev.filter((item) => item.id !== selectedId));
    setSelectedId(null);
  }, [annotations, pushUndo, selectedId]);

  const handleClear = useCallback(() => {
    if (annotations.length === 0) return;
    pushUndo(annotations);
    setAnnotations([]);
    setSelectedId(null);
  }, [annotations, pushUndo]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const cmd = event.ctrlKey || event.metaKey;
      if (cmd && event.key === 'z' && !event.shiftKey) { event.preventDefault(); handleUndo(); return; }
      if (cmd && (event.key === 'y' || (event.key === 'z' && event.shiftKey))) { event.preventDefault(); handleRedo(); return; }
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId && !textInput) {
        event.preventDefault();
        handleDeleteSelected();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleDeleteSelected, handleRedo, handleUndo, selectedId, textInput]);

  const handleConfirm = useCallback(async () => {
    setProcessing(true); setError(null);
    try {
      if (!imageEl) throw new Error('Image not loaded');
      const canvas = document.createElement('canvas');
      canvas.width = imageEl.naturalWidth;
      canvas.height = imageEl.naturalHeight;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('Canvas context failed');
      ctx.drawImage(imageEl, 0, 0);
      const scaleX = imageEl.naturalWidth / canvasSize.width;
      const scaleY = imageEl.naturalHeight / canvasSize.height;

      for (const item of annotations) {
        ctx.save();
        ctx.globalAlpha = item.opacity ?? 1;
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
          const fSize = (item.fontSize ?? 16) * Math.max(scaleX, scaleY);
          ctx.font = `bold ${fSize}px sans-serif`;
          ctx.fillText(item.text, (item.x ?? 0) * scaleX, (item.y ?? 0) * scaleY);
        }
        ctx.restore();
      }

      onConfirm(canvasToDataUrl(canvas));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Annotation failed');
    } finally {
      setProcessing(false);
    }
  }, [annotations, canvasSize, imageEl, onConfirm]);

  const cursorClass = activeTool === 'text' ? 'cursor-text' :
    activeTool === 'select' ? 'cursor-default' :
    activeTool === 'eraser' ? 'cursor-pointer' : 'cursor-crosshair';

  // Group tool buttons by separator
  const toolGroups = useMemo(() => {
    const groups: Array<Array<typeof TOOL_BUTTONS[0]>> = [];
    let currentGroup: string | null = null;
    let currentButtons: Array<typeof TOOL_BUTTONS[0]> = [];
    for (const btn of TOOL_BUTTONS) {
      if (btn.group !== currentGroup) {
        if (currentButtons.length > 0) groups.push(currentButtons);
        currentButtons = [];
        currentGroup = btn.group;
      }
      currentButtons.push(btn);
    }
    if (currentButtons.length > 0) groups.push(currentButtons);
    return groups;
  }, []);

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-text-dark">
        <PenLine className="h-4 w-4" />
        <span>Annotate Image</span>
      </div>

      {/* Tool bar with separator groups */}
      <div className="flex items-center gap-0.5">
        {toolGroups.map((group, gi) => (
          <div key={gi} className="flex items-center gap-0.5">
            {gi > 0 && <div className="mx-1 h-5 w-px bg-[rgba(255,255,255,0.12)]" />}
            {group.map((tool) => {
              const Icon = tool.icon;
              return (
                <button key={tool.type} type="button" onClick={() => { setActiveTool(tool.type); if (tool.type !== 'select') setSelectedId(null); }}
                  className={`flex items-center gap-1 rounded-full px-2 py-1 text-[11px] transition-colors ${
                    activeTool === tool.type
                      ? 'bg-indigo-600 text-white'
                      : 'bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]'
                  }`}
                  title={`${tool.label}${tool.type === 'select' ? ' (move annotations)' : ''}`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {tool.label}
                </button>
              );
            })}
          </div>
        ))}
        <div className="ml-auto flex items-center gap-1">
          <button type="button" onClick={handleUndo} disabled={!canUndo}
            className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:bg-[rgba(255,255,255,0.1)] disabled:opacity-30"
            title="Undo (Ctrl+Z)"
          ><Undo2 className="h-3.5 w-3.5" /></button>
          <button type="button" onClick={handleRedo} disabled={!canRedo}
            className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:bg-[rgba(255,255,255,0.1)] disabled:opacity-30"
            title="Redo (Ctrl+Shift+Z)"
          ><Redo2 className="h-3.5 w-3.5" /></button>
          <button type="button" onClick={handleDeleteSelected} disabled={!selectedId}
            className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:bg-[rgba(255,255,255,0.1)] disabled:opacity-30"
            title="Delete selected (Del)"
          ><Trash2 className="h-3.5 w-3.5" /></button>
          <button type="button" onClick={handleClear} disabled={annotations.length === 0}
            className="flex h-6 w-6 items-center justify-center rounded text-red-400 hover:bg-[rgba(255,255,255,0.1)] disabled:opacity-30"
            title="Clear all"
          ><X className="h-3.5 w-3.5" /></button>
        </div>
      </div>

      {/* Color + stroke + opacity */}
      <div className="flex flex-wrap items-center gap-3">
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

        {activeTool !== 'text' && activeTool !== 'select' && activeTool !== 'eraser' && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-text-muted">Width:</span>
            {STROKE_WIDTHS.map((w) => (
              <button key={w} type="button" onClick={() => setLineWidth(w)}
                className={`flex h-6 w-6 items-center justify-center rounded text-[10px] ${lineWidth === w ? 'bg-indigo-600 text-white' : 'bg-[rgba(255,255,255,0.08)] text-text-muted'}`}
              >{w}</button>
            ))}
          </div>
        )}

        {activeTool === 'text' && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-text-muted">Size:</span>
            <select
              value={fontSize}
              onChange={(e) => setFontSize(Number(e.target.value))}
              className="h-6 rounded border border-[rgba(255,255,255,0.14)] bg-bg-dark/80 px-1 text-[10px] text-text-dark outline-none"
            >
              {FONT_SIZES.map((s) => (
                <option key={s} value={s}>{s}px</option>
              ))}
            </select>
          </div>
        )}

        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-text-muted">Opacity:</span>
          <input
            type="range"
            min={0.1}
            max={1}
            step={0.05}
            value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))}
            className="h-1 w-16 cursor-pointer"
          />
          <span className="text-[10px] text-text-muted w-7">{Math.round(opacity * 100)}%</span>
        </div>
      </div>

      {/* Canvas */}
      <div ref={containerRef} className="relative overflow-hidden rounded-lg border border-[rgba(255,255,255,0.1)] bg-bg-dark/60">
        <canvas
          ref={canvasRef}
          width={canvasSize.width}
          height={canvasSize.height}
          className={`block w-full ${cursorClass}`}
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
              fontSize: `${Math.max(10, fontSize * (containerRef.current ? containerRef.current.clientWidth / canvasSize.width : 1) * 0.7)}px`,
            }}
          />
        )}
      </div>

      {/* Info bar */}
      <div className="flex items-center gap-3 text-[10px] text-text-muted">
        <span>{annotations.length} annotation{annotations.length !== 1 ? 's' : ''}</span>
        {selectedAnnotation && (
          <span>Selected: {selectedAnnotation.type}</span>
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
