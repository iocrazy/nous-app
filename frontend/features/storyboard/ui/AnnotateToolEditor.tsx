import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import {
  Stage, Layer, Rect, Ellipse, Line, Arrow, Text,
  Image as KonvaImage, Transformer, Group,
} from 'react-konva';
import type { KonvaEventObject } from 'konva/lib/Node';
import type Konva from 'konva';
import {
  ArrowRight, Check, Circle, Eraser, MousePointer2, Minus as LineIcon,
  PenLine, Square, Trash2, Type, Undo2, Redo2, X,
} from 'lucide-react';
import { UiButton } from '../../../components/ui';
import { loadImageElement, canvasToDataUrl } from '../application/imageData';
import {
  type AnnotationItem,
  type AnnotationToolType,
  type DraftState,
  buildAnnotationFromDraft,
  clamp,
  createAnnotationId,
  flattenAnnotationsToCanvas,
  moveAnnotation,
  transformAnnotation,
  normalizeRect,
  PRESET_COLORS,
  STROKE_WIDTH_OPTIONS,
  FONT_SIZE_OPTIONS,
  MAX_UNDO_STACK,
} from './annotation/konvaShapes';

// ─── Props ──────────────────────────────────────────────────────────────────

interface AnnotateToolEditorProps {
  imageUrl: string;
  onConfirm: (resultUrl: string) => void;
  onCancel: () => void;
}

// ─── Constants ──────────────────────────────────────────────────────────────

const VIEWPORT_PADDING = 16;
const VIEWPORT_MIN_W = 220;
const VIEWPORT_MIN_H = 180;

interface ToolButton {
  type: AnnotationToolType;
  label: string;
  icon: typeof Square;
  group: 'select' | 'draw' | 'shape' | 'other';
}

const TOOL_BUTTONS: ToolButton[] = [
  { type: 'select', label: 'Select', icon: MousePointer2, group: 'select' },
  { type: 'pen', label: 'Pen', icon: PenLine, group: 'draw' },
  { type: 'line', label: 'Line', icon: LineIcon, group: 'draw' },
  { type: 'eraser', label: 'Eraser', icon: Eraser, group: 'draw' },
  { type: 'rect', label: 'Rect', icon: Square, group: 'shape' },
  { type: 'ellipse', label: 'Ellipse', icon: Circle, group: 'shape' },
  { type: 'arrow', label: 'Arrow', icon: ArrowRight, group: 'shape' },
  { type: 'text', label: 'Text', icon: Type, group: 'other' },
];

// ─── Tool button groups ─────────────────────────────────────────────────────

function groupToolButtons(buttons: ToolButton[]): ToolButton[][] {
  const groups: ToolButton[][] = [];
  let current: ToolButton[] = [];
  let currentGroup: string | null = null;
  for (const btn of buttons) {
    if (btn.group !== currentGroup) {
      if (current.length > 0) groups.push(current);
      current = [];
      currentGroup = btn.group;
    }
    current.push(btn);
  }
  if (current.length > 0) groups.push(current);
  return groups;
}

const TOOL_GROUPS = groupToolButtons(TOOL_BUTTONS);

// ─── Component ──────────────────────────────────────────────────────────────

export function AnnotateToolEditor({ imageUrl, onConfirm, onCancel }: AnnotateToolEditorProps) {
  // Image state
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [processing, setProcessing] = useState(false);

  // Tool state
  const [activeTool, setActiveTool] = useState<AnnotationToolType>('pen');
  const [color, setColor] = useState('#FF4444');
  const [strokeWidth, setStrokeWidth] = useState(4);
  const [fontSize, setFontSize] = useState(20);
  const [opacity, setOpacity] = useState(1);

  // Annotations
  const [annotations, setAnnotations] = useState<AnnotationItem[]>([]);
  const [undoStack, setUndoStack] = useState<AnnotationItem[][]>([]);
  const [redoStack, setRedoStack] = useState<AnnotationItem[][]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftState | null>(null);

  // Text editing
  const [textInput, setTextInput] = useState<{ x: number; y: number; value: string } | null>(null);
  const textInputRef = useRef<HTMLTextAreaElement>(null);

  // Viewport sizing
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const viewportRef = useRef<HTMLDivElement>(null);

  // Konva refs
  const stageRef = useRef<Konva.Stage | null>(null);
  const contentGroupRef = useRef<Konva.Group | null>(null);
  const transformerRef = useRef<Konva.Transformer | null>(null);
  const shapeRefs = useRef<Map<string, Konva.Node>>(new Map());
  const stageHostRef = useRef<HTMLDivElement>(null);

  const canUndo = undoStack.length > 0;
  const canRedo = redoStack.length > 0;
  const selectedAnnotation = useMemo(
    () => annotations.find((a) => a.id === selectedId) ?? null,
    [annotations, selectedId],
  );

  // ─── Load image ─────────────────────────────────────────────────────────

  useEffect(() => {
    void loadImageElement(imageUrl)
      .then((img) => setImage(img))
      .catch(() => setError('Failed to load image'));
  }, [imageUrl]);

  // ─── Viewport sizing ───────────────────────────────────────────────────

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const update = () => {
      const rect = el.getBoundingClientRect();
      setViewportSize({ width: Math.max(0, Math.round(rect.width)), height: Math.max(0, Math.round(rect.height)) });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const { stageWidth, stageHeight, scale } = useMemo(() => {
    if (!image) return { stageWidth: 560, stageHeight: 400, scale: 1 };
    const maxW = Math.max(VIEWPORT_MIN_W, viewportSize.width - VIEWPORT_PADDING * 2);
    const maxH = Math.max(VIEWPORT_MIN_H, viewportSize.height - VIEWPORT_PADDING * 2);
    const ratio = Math.min(maxW / image.naturalWidth, maxH / image.naturalHeight, 1);
    return {
      stageWidth: Math.max(1, Math.round(image.naturalWidth * ratio)),
      stageHeight: Math.max(1, Math.round(image.naturalHeight * ratio)),
      scale: ratio,
    };
  }, [image, viewportSize]);

  // ─── History helpers ────────────────────────────────────────────────────

  const pushUndo = useCallback((current: AnnotationItem[]) => {
    setUndoStack((prev) => [...prev, current].slice(-MAX_UNDO_STACK));
    setRedoStack([]);
  }, []);

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
    setAnnotations((prev) => prev.filter((a) => a.id !== selectedId));
    setSelectedId(null);
  }, [annotations, pushUndo, selectedId]);

  const handleClear = useCallback(() => {
    if (annotations.length === 0) return;
    pushUndo(annotations);
    setAnnotations([]);
    setSelectedId(null);
  }, [annotations, pushUndo]);

  // ─── Image coordinate conversion ───────────────────────────────────────

  const getImagePoint = useCallback(() => {
    const stage = stageRef.current;
    const group = contentGroupRef.current;
    if (!stage || !group || !image) return null;
    const pointer = stage.getPointerPosition();
    if (!pointer) return null;
    const transform = group.getAbsoluteTransform().copy();
    transform.invert();
    const pt = transform.point(pointer);
    return {
      x: clamp(pt.x, 0, image.naturalWidth),
      y: clamp(pt.y, 0, image.naturalHeight),
    };
  }, [image]);

  // ─── Text editor helpers ──────────────────────────────────────────────

  const toHostPoint = useCallback((x: number, y: number) => {
    const group = contentGroupRef.current;
    const stage = stageRef.current;
    const host = stageHostRef.current;
    const stagePoint = group
      ? group.getAbsoluteTransform().point({ x, y })
      : { x: x * scale, y: y * scale };
    if (!stage || !host) return stagePoint;
    const stageRect = stage.container().getBoundingClientRect();
    const hostRect = host.getBoundingClientRect();
    return {
      x: stagePoint.x + (stageRect.left - hostRect.left),
      y: stagePoint.y + (stageRect.top - hostRect.top),
    };
  }, [scale]);

  const textEditorPos = useMemo(() => {
    if (!textInput) return null;
    return toHostPoint(textInput.x, textInput.y);
  }, [textInput, toHostPoint]);

  const handleTextCommit = useCallback(() => {
    if (!textInput) return;
    const value = textInput.value.trim();
    if (!value) { setTextInput(null); return; }
    pushUndo(annotations);
    const newItem: AnnotationItem = {
      id: createAnnotationId(),
      type: 'text',
      x: textInput.x,
      y: textInput.y,
      text: value,
      fontSize,
      color,
      strokeWidth,
      opacity,
    };
    setAnnotations((prev) => [...prev, newItem]);
    setSelectedId(newItem.id);
    setTextInput(null);
  }, [annotations, color, fontSize, opacity, pushUndo, strokeWidth, textInput]);

  // ─── Pointer handlers ────────────────────────────────────────────────

  const handlePointerDown = useCallback((e: KonvaEventObject<MouseEvent | TouchEvent>) => {
    stageHostRef.current?.focus();
    const point = getImagePoint();
    if (!point) return;

    const target = e.target;
    const isBg = target === target.getStage() || target.name() === 'annotation-background';

    if (activeTool === 'text') {
      if (isBg) {
        setTextInput({ x: point.x, y: point.y, value: '' });
        requestAnimationFrame(() => textInputRef.current?.focus());
      }
      return;
    }

    if (activeTool === 'eraser') {
      // Find annotation under pointer via Konva hit detection
      if (!isBg) {
        const clickedId = target.id();
        if (clickedId) {
          pushUndo(annotations);
          setAnnotations((prev) => prev.filter((a) => a.id !== clickedId));
          if (selectedId === clickedId) setSelectedId(null);
        }
      }
      return;
    }

    if (activeTool === 'select') {
      if (isBg) {
        setSelectedId(null);
      }
      // Konva handles selection via shape click events
      return;
    }

    // Drawing tools — only start on background
    if (!isBg) return;
    setTextInput(null);
    setSelectedId(null);
    setDraft({
      tool: activeTool as DraftState['tool'],
      startX: point.x,
      startY: point.y,
      currentX: point.x,
      currentY: point.y,
      points: activeTool === 'pen' ? [point.x, point.y] : undefined,
    });
  }, [activeTool, annotations, getImagePoint, pushUndo, selectedId]);

  const handlePointerMove = useCallback(() => {
    if (!draft) return;
    const point = getImagePoint();
    if (!point) return;

    if (draft.tool === 'pen') {
      setDraft((prev) => prev && prev.tool === 'pen'
        ? { ...prev, currentX: point.x, currentY: point.y, points: [...(prev.points ?? []), point.x, point.y] }
        : prev,
      );
    } else {
      setDraft((prev) => prev ? { ...prev, currentX: point.x, currentY: point.y } : prev);
    }
  }, [draft, getImagePoint]);

  const handlePointerUp = useCallback(() => {
    if (!draft) return;
    const point = getImagePoint();
    const finalDraft: DraftState = {
      ...draft,
      currentX: point?.x ?? draft.currentX,
      currentY: point?.y ?? draft.currentY,
    };
    const item = buildAnnotationFromDraft(finalDraft, color, strokeWidth, opacity);
    setDraft(null);
    if (!item) return;
    pushUndo(annotations);
    setAnnotations((prev) => [...prev, item]);
    setSelectedId(item.id);
  }, [annotations, color, draft, getImagePoint, opacity, pushUndo, strokeWidth]);

  // ─── Draft annotation for live preview ────────────────────────────────

  const draftAnnotation = useMemo((): AnnotationItem | null => {
    if (!draft) return null;
    if (draft.tool === 'pen') {
      const pts = draft.points ?? [draft.startX, draft.startY];
      if (pts.length < 2) return null;
      return { id: 'draft', type: 'pen', points: pts, color, strokeWidth, opacity };
    }
    if (draft.tool === 'line') {
      return { id: 'draft', type: 'line', points: [draft.startX, draft.startY, draft.currentX, draft.currentY], color, strokeWidth, opacity };
    }
    if (draft.tool === 'arrow') {
      return { id: 'draft', type: 'arrow', points: [draft.startX, draft.startY, draft.currentX, draft.currentY], color, strokeWidth, opacity };
    }
    const rect = normalizeRect(draft.startX, draft.startY, draft.currentX, draft.currentY);
    if (draft.tool === 'rect') {
      return { id: 'draft', type: 'rect', ...rect, color, strokeWidth, opacity };
    }
    return { id: 'draft', type: 'ellipse', ...rect, color, strokeWidth, opacity };
  }, [color, draft, opacity, strokeWidth]);

  // ─── Konva shape refs ─────────────────────────────────────────────────

  const bindShapeRef = useCallback((id: string, node: Konva.Node | null) => {
    if (node) shapeRefs.current.set(id, node);
    else shapeRefs.current.delete(id);
  }, []);

  // ─── Shape drag/transform end handlers ────────────────────────────────

  const handleDragEnd = useCallback((item: AnnotationItem, e: KonvaEventObject<DragEvent>) => {
    const node = e.target;
    const nx = node.x();
    const ny = node.y();
    if (item.type === 'pen' || item.type === 'line' || item.type === 'arrow') {
      node.x(0);
      node.y(0);
    }
    pushUndo(annotations);
    setAnnotations((prev) => prev.map((a) => (a.id === item.id ? moveAnnotation(a, nx, ny) : a)));
  }, [annotations, pushUndo]);

  const handleTransformEnd = useCallback((item: AnnotationItem, e: KonvaEventObject<Event>) => {
    const node = e.target;
    const sx = node.scaleX();
    const sy = node.scaleY();
    const nx = node.x();
    const ny = node.y();
    node.scaleX(1);
    node.scaleY(1);
    if (item.type === 'pen' || item.type === 'line' || item.type === 'arrow') {
      node.x(0);
      node.y(0);
    }
    pushUndo(annotations);
    setAnnotations((prev) => prev.map((a) => (a.id === item.id ? transformAnnotation(a, nx, ny, sx, sy) : a)));
  }, [annotations, pushUndo]);

  // ─── Sync transformer ────────────────────────────────────────────────

  useEffect(() => {
    const tr = transformerRef.current;
    if (!tr) return;
    if (!selectedId || activeTool !== 'select') {
      tr.nodes([]);
      tr.getLayer()?.batchDraw();
      return;
    }
    const node = shapeRefs.current.get(selectedId);
    if (!node) {
      tr.nodes([]);
      tr.getLayer()?.batchDraw();
      return;
    }
    tr.nodes([node]);
    tr.getLayer()?.batchDraw();
  }, [selectedId, activeTool, annotations]);

  // ─── Keyboard shortcuts ───────────────────────────────────────────────

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (textInput) return;
    const cmd = e.ctrlKey || e.metaKey;
    if (cmd && e.key === 'z' && !e.shiftKey) { e.preventDefault(); handleUndo(); return; }
    if (cmd && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) { e.preventDefault(); handleRedo(); return; }
    if ((e.key === 'Delete' || e.key === 'Backspace') && selectedId) { e.preventDefault(); handleDeleteSelected(); }
  }, [handleDeleteSelected, handleRedo, handleUndo, selectedId, textInput]);

  // ─── Render a single annotation shape ─────────────────────────────────

  const renderShape = useCallback((item: AnnotationItem, itemOpacity = 1) => {
    const isSelected = selectedId === item.id;
    const canDrag = activeTool === 'select' && isSelected;

    const commonProps = {
      draggable: canDrag,
      onClick: () => { if (activeTool === 'select') setSelectedId(item.id); },
      onTap: () => { if (activeTool === 'select') setSelectedId(item.id); },
      onDragEnd: (e: KonvaEventObject<DragEvent>) => handleDragEnd(item, e),
      onTransformEnd: (e: KonvaEventObject<Event>) => handleTransformEnd(item, e),
    };

    if (item.type === 'pen') {
      return (
        <Line
          key={item.id}
          id={item.id}
          ref={(n) => bindShapeRef(item.id, n)}
          points={item.points}
          stroke={item.color}
          strokeWidth={item.strokeWidth}
          lineJoin="round"
          lineCap="round"
          opacity={item.opacity * itemOpacity}
          strokeScaleEnabled={false}
          {...commonProps}
        />
      );
    }

    if (item.type === 'line') {
      return (
        <Line
          key={item.id}
          id={item.id}
          ref={(n) => bindShapeRef(item.id, n)}
          points={item.points}
          stroke={item.color}
          strokeWidth={item.strokeWidth}
          lineCap="round"
          opacity={item.opacity * itemOpacity}
          strokeScaleEnabled={false}
          {...commonProps}
        />
      );
    }

    if (item.type === 'arrow') {
      return (
        <Arrow
          key={item.id}
          id={item.id}
          ref={(n) => bindShapeRef(item.id, n)}
          points={item.points}
          stroke={item.color}
          fill={item.color}
          strokeWidth={item.strokeWidth}
          pointerLength={Math.max(10, item.strokeWidth * 4)}
          pointerWidth={Math.max(10, item.strokeWidth * 3)}
          opacity={item.opacity * itemOpacity}
          strokeScaleEnabled={false}
          {...commonProps}
        />
      );
    }

    if (item.type === 'rect') {
      return (
        <Rect
          key={item.id}
          id={item.id}
          ref={(n) => bindShapeRef(item.id, n)}
          x={item.x}
          y={item.y}
          width={item.width}
          height={item.height}
          stroke={item.color}
          strokeWidth={item.strokeWidth}
          opacity={item.opacity * itemOpacity}
          strokeScaleEnabled={false}
          {...commonProps}
        />
      );
    }

    if (item.type === 'ellipse') {
      return (
        <Ellipse
          key={item.id}
          id={item.id}
          ref={(n) => bindShapeRef(item.id, n)}
          x={item.x + item.width / 2}
          y={item.y + item.height / 2}
          radiusX={item.width / 2}
          radiusY={item.height / 2}
          stroke={item.color}
          strokeWidth={item.strokeWidth}
          opacity={item.opacity * itemOpacity}
          strokeScaleEnabled={false}
          {...commonProps}
        />
      );
    }

    // text
    return (
      <Text
        key={item.id}
        id={item.id}
        ref={(n) => bindShapeRef(item.id, n)}
        x={item.x}
        y={item.y}
        text={item.text}
        fill={item.color}
        fontSize={item.fontSize}
        fontStyle="bold"
        lineHeight={1.2}
        opacity={item.opacity * itemOpacity}
        {...commonProps}
        onDblClick={(e) => {
          e.cancelBubble = true;
          setTextInput({ x: item.x, y: item.y, value: item.text });
          setSelectedId(item.id);
          // Remove old text, will be re-added on commit
          pushUndo(annotations);
          setAnnotations((prev) => prev.filter((a) => a.id !== item.id));
          requestAnimationFrame(() => textInputRef.current?.focus());
        }}
      />
    );
  }, [activeTool, annotations, bindShapeRef, handleDragEnd, handleTransformEnd, pushUndo, selectedId]);

  // ─── Export ───────────────────────────────────────────────────────────

  const handleConfirm = useCallback(async () => {
    setProcessing(true);
    setError(null);
    try {
      if (!image) throw new Error('Image not loaded');
      const canvas = flattenAnnotationsToCanvas(image, annotations);
      onConfirm(canvasToDataUrl(canvas));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Annotation export failed');
    } finally {
      setProcessing(false);
    }
  }, [annotations, image, onConfirm]);

  // ─── Transformer config ───────────────────────────────────────────────

  const transformerKeepRatio = selectedAnnotation?.type === 'text';
  const transformerAnchors = transformerKeepRatio
    ? (['top-left', 'top-right', 'bottom-left', 'bottom-right'] as const)
    : (['top-left', 'top-center', 'top-right', 'middle-right', 'bottom-right', 'bottom-center', 'bottom-left', 'middle-left'] as const);

  // ─── Cursor ───────────────────────────────────────────────────────────

  const cursorClass = activeTool === 'text' ? 'cursor-text'
    : activeTool === 'select' ? 'cursor-default'
    : activeTool === 'eraser' ? 'cursor-pointer'
    : 'cursor-crosshair';

  // ─── Render ───────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-text-dark">
        <PenLine className="h-4 w-4" />
        <span>Annotate Image</span>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-0.5">
        {TOOL_GROUPS.map((group, gi) => (
          <div key={gi} className="flex items-center gap-0.5">
            {gi > 0 && <div className="mx-1 h-5 w-px bg-[rgba(255,255,255,0.12)]" />}
            {group.map((tool) => {
              const Icon = tool.icon;
              return (
                <button
                  key={tool.type}
                  type="button"
                  onClick={() => { setActiveTool(tool.type); if (tool.type !== 'select') setSelectedId(null); }}
                  className={`flex items-center gap-1 rounded-full px-2 py-1 text-[11px] transition-colors ${
                    activeTool === tool.type
                      ? 'bg-indigo-600 text-white'
                      : 'bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]'
                  }`}
                  title={tool.label}
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
            title="Undo (Ctrl+Z)"><Undo2 className="h-3.5 w-3.5" /></button>
          <button type="button" onClick={handleRedo} disabled={!canRedo}
            className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:bg-[rgba(255,255,255,0.1)] disabled:opacity-30"
            title="Redo (Ctrl+Shift+Z)"><Redo2 className="h-3.5 w-3.5" /></button>
          <button type="button" onClick={handleDeleteSelected} disabled={!selectedId}
            className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:bg-[rgba(255,255,255,0.1)] disabled:opacity-30"
            title="Delete selected"><Trash2 className="h-3.5 w-3.5" /></button>
          <button type="button" onClick={handleClear} disabled={annotations.length === 0}
            className="flex h-6 w-6 items-center justify-center rounded text-red-400 hover:bg-[rgba(255,255,255,0.1)] disabled:opacity-30"
            title="Clear all"><X className="h-3.5 w-3.5" /></button>
        </div>
      </div>

      {/* Style controls */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Color presets + custom */}
        <div className="flex items-center gap-1">
          {PRESET_COLORS.map((c) => (
            <button key={c} type="button" onClick={() => setColor(c)}
              className={`h-5 w-5 rounded-full border-2 transition-transform ${color === c ? 'scale-110 border-white' : 'border-transparent'}`}
              style={{ backgroundColor: c }} />
          ))}
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)}
            className="ml-1 h-5 w-5 cursor-pointer rounded border-none bg-transparent" />
        </div>

        {/* Stroke width */}
        {activeTool !== 'text' && activeTool !== 'select' && activeTool !== 'eraser' && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-text-muted">Width:</span>
            {STROKE_WIDTH_OPTIONS.map((w) => (
              <button key={w} type="button" onClick={() => setStrokeWidth(w)}
                className={`flex h-6 w-6 items-center justify-center rounded text-[10px] ${strokeWidth === w ? 'bg-indigo-600 text-white' : 'bg-[rgba(255,255,255,0.08)] text-text-muted'}`}
              >{w}</button>
            ))}
          </div>
        )}

        {/* Font size */}
        {activeTool === 'text' && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-text-muted">Size:</span>
            <select value={fontSize} onChange={(e) => setFontSize(Number(e.target.value))}
              className="h-6 rounded border border-[rgba(255,255,255,0.14)] bg-bg-dark/80 px-1 text-[10px] text-text-dark outline-none">
              {FONT_SIZE_OPTIONS.map((s) => <option key={s} value={s}>{s}px</option>)}
            </select>
          </div>
        )}

        {/* Opacity */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-text-muted">Opacity:</span>
          <input type="range" min={0.1} max={1} step={0.05} value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))} className="h-1 w-16 cursor-pointer" />
          <span className="w-7 text-[10px] text-text-muted">{Math.round(opacity * 100)}%</span>
        </div>
      </div>

      {/* Konva Canvas */}
      <div ref={viewportRef} className="relative h-[min(58vh,560px)] overflow-hidden rounded-lg border border-[rgba(255,255,255,0.1)] bg-bg-dark/60">
        <div
          ref={stageHostRef}
          tabIndex={0}
          className={`relative flex h-full w-full items-center justify-center p-2 outline-none ${cursorClass}`}
          onKeyDown={handleKeyDown}
        >
          <Stage
            ref={stageRef}
            width={stageWidth}
            height={stageHeight}
            onMouseDown={handlePointerDown}
            onTouchStart={handlePointerDown}
            onMouseMove={handlePointerMove}
            onTouchMove={handlePointerMove}
            onMouseUp={handlePointerUp}
            onTouchEnd={handlePointerUp}
            onMouseLeave={handlePointerUp}
          >
            <Layer>
              <Group ref={contentGroupRef} scaleX={scale} scaleY={scale}>
                {image && (
                  <KonvaImage
                    image={image}
                    x={0}
                    y={0}
                    width={image.naturalWidth}
                    height={image.naturalHeight}
                    name="annotation-background"
                  />
                )}
                {annotations.map((a) => renderShape(a))}
                {draftAnnotation && renderShape(draftAnnotation, 0.7)}
                <Transformer
                  ref={transformerRef}
                  boundBoxFunc={(oldBox, newBox) => (newBox.width < 5 || newBox.height < 5) ? oldBox : newBox}
                  rotateEnabled={false}
                  borderStroke="#6366f1"
                  anchorStroke="#6366f1"
                  anchorFill="#ffffff"
                  anchorSize={8}
                  ignoreStroke
                  keepRatio={transformerKeepRatio}
                  enabledAnchors={[...transformerAnchors]}
                />
              </Group>
            </Layer>
          </Stage>

          {/* Text editing overlay */}
          {textInput && textEditorPos && (
            <div
              className="absolute z-20 flex flex-col gap-2 rounded-md border border-[rgba(255,255,255,0.2)] bg-black/75 p-2 backdrop-blur-sm"
              style={{ left: `${textEditorPos.x}px`, top: `${textEditorPos.y}px`, transform: 'translate(0, -100%)', minWidth: 160, maxWidth: 280 }}
            >
              <textarea
                ref={textInputRef}
                value={textInput.value}
                onChange={(e) => setTextInput((prev) => prev ? { ...prev, value: e.target.value } : null)}
                onKeyDown={(e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); handleTextCommit(); }
                  if (e.key === 'Escape') { e.preventDefault(); setTextInput(null); }
                }}
                rows={2}
                className="w-full resize-none rounded border border-[rgba(255,255,255,0.18)] bg-bg-dark/90 px-2 py-1.5 text-sm text-text-dark outline-none focus:border-indigo-400"
                placeholder="Enter text..."
              />
              <div className="flex items-center justify-end gap-2">
                <button type="button" onClick={() => setTextInput(null)}
                  className="rounded border border-[rgba(255,255,255,0.22)] px-2 py-1 text-xs text-text-muted hover:bg-bg-dark">Cancel</button>
                <button type="button" onClick={handleTextCommit}
                  className="rounded border border-indigo-400/45 bg-indigo-500/20 px-2 py-1 text-xs text-text-dark hover:bg-indigo-500/30">OK</button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Status bar */}
      <div className="flex items-center gap-3 text-[10px] text-text-muted">
        <span>{annotations.length} annotation{annotations.length !== 1 ? 's' : ''}</span>
        {selectedAnnotation && <span>Selected: {selectedAnnotation.type}</span>}
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      {/* Action buttons */}
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
