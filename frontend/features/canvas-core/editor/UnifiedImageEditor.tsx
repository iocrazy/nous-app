// features/canvas-core/editor/UnifiedImageEditor.tsx
//
// IC-parity unified image editor (B): ONE modal, seven inline tabs —
// Preview / Crop / Expand / Mask / Brush / Resize / Split (IC's
// imageEditModal tab bar). The four existing tool kernels mount directly
// (they are controlled src/value/onChange components); Brush and Resize are
// the two new client-side modes (PaintTool + imageBake). Commit channels
// are per-mode props — an absent channel hides its tab, so callers without
// a derive path (e.g. media cards until the resources bridge lands) get an
// honest subset instead of dead buttons.

import {
  Brush as BrushIcon,
  Crop as CropIcon,
  Download,
  Expand,
  Eye,
  Grid3x3,
  Minimize2,
  Paintbrush,
  X,
} from 'lucide-react';
import { useEffect, useState } from 'react';

import { mediaSrc } from '../smart/mediaUrl';
import { CropTool } from './CropTool';
import { GridSplitTool } from './GridSplitTool';
import { MaskBrushTool } from './MaskBrushTool';
import { OutpaintTool } from './OutpaintTool';
import { PaintTool, type PaintShape, type PaintShapeTool } from './PaintTool';
import { snapRegionToAspect } from './cropMath';
import { FULL_REGION, type CropRegion } from './types';
import { EMPTY_GRID, presetGrid, type GridLines } from './gridMath';
import {
  BRUSH_SIZES,
  FALLBACK_MASK_SIZE,
  hasMaskContent,
  type MaskStroke,
  type MaskTool,
} from './maskMath';
import { ZERO_PADDING, hasExtension, type OutpaintPadding } from './outpaintMath';

export type EditorMode =
  | 'preview'
  | 'crop'
  | 'outpaint'
  | 'mask'
  | 'brush'
  | 'resize'
  | 'split';

export interface UnifiedImageEditorProps {
  open: boolean;
  src: string;
  alt?: string;
  initialMode?: EditorMode;
  /** Seed for the crop rectangle (the node's persisted crop_region). */
  cropInitialRegion?: CropRegion;
  /** Seed for the outpaint prompt (the node's caption). */
  outpaintInitialPrompt?: string;
  onClose(): void;
  onCropCommit?(region: CropRegion): void;
  onOutpaintCommit?(padding: OutpaintPadding, prompt: string): void;
  onMaskCommit?(strokes: MaskStroke[], size: { width: number; height: number }): void;
  onSplitCommit?(lines: GridLines): void;
  onBrushCommit?(shapes: PaintShape[]): void;
  onResizeCommit?(scale: number): void;
  committing?: boolean;
}

const MODE_META: Array<{
  mode: EditorMode;
  label: string;
  icon: React.ReactNode;
  apply?: string;
}> = [
  { mode: 'preview', label: 'Preview', icon: <Eye size={13} /> },
  { mode: 'crop', label: 'Crop', icon: <CropIcon size={13} />, apply: 'Apply Crop' },
  { mode: 'outpaint', label: 'Expand', icon: <Expand size={13} />, apply: 'Apply Expand' },
  { mode: 'mask', label: 'Mask', icon: <BrushIcon size={13} />, apply: 'Cut Out' },
  { mode: 'brush', label: 'Brush', icon: <Paintbrush size={13} />, apply: 'Apply Brush' },
  { mode: 'resize', label: 'Resize', icon: <Minimize2 size={13} />, apply: 'Apply Resize' },
  { mode: 'split', label: 'Split', icon: <Grid3x3 size={13} />, apply: 'Split' },
];

const PAINT_TOOLS: Array<{ tool: PaintShapeTool; label: string }> = [
  { tool: 'free', label: 'Free' },
  { tool: 'rect', label: 'Rect' },
  { tool: 'ellipse', label: 'Ellipse' },
  { tool: 'label', label: 'Number' },
  { tool: 'text', label: 'Text' },
];

export function UnifiedImageEditor({
  open,
  src,
  alt = '',
  initialMode = 'preview',
  cropInitialRegion,
  outpaintInitialPrompt = '',
  onClose,
  onCropCommit,
  onOutpaintCommit,
  onMaskCommit,
  onSplitCommit,
  onBrushCommit,
  onResizeCommit,
  committing = false,
}: UnifiedImageEditorProps) {
  const [mode, setMode] = useState<EditorMode>(initialMode);
  const [region, setRegion] = useState<CropRegion>(FULL_REGION);
  const [padding, setPadding] = useState<OutpaintPadding>(ZERO_PADDING);
  const [outpaintPrompt, setOutpaintPrompt] = useState('');
  const [strokes, setStrokes] = useState<MaskStroke[]>([]);
  // IC edit-draw history: undo pops into the redo stack; a new stroke
  // clears redo (standard editor semantics, EDIT_DRAW_HISTORY_MAX-ish).
  const [strokeRedo, setStrokeRedo] = useState<MaskStroke[][]>([]);
  const [maskTool] = useState<MaskTool>('brush');
  const [maskSize, setMaskSize] = useState<number>(BRUSH_SIZES[1].value);
  const [shapes, setShapes] = useState<PaintShape[]>([]);
  const [shapeRedo, setShapeRedo] = useState<PaintShape[][]>([]);
  // IC crop aspect presets — null = free.
  const [cropAspect, setCropAspect] = useState<number | null>(null);
  const [paintTool, setPaintTool] = useState<PaintShapeTool>('free');
  const [paintColor, setPaintColor] = useState('#ff2d55');
  const [paintSize, setPaintSize] = useState(14);
  const [lines, setLines] = useState<GridLines>(EMPTY_GRID);
  const [scale, setScale] = useState(0.5);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  // IC chrome: preview wheel-zoom percentage (100 = fit).
  const [zoom, setZoom] = useState(100);

  // Fresh session on every open (same contract as the standalone modals).
  useEffect(() => {
    if (open) {
      setMode(initialMode);
      setRegion(cropInitialRegion ?? FULL_REGION);
      setPadding(ZERO_PADDING);
      setOutpaintPrompt(outpaintInitialPrompt);
      setStrokes([]);
      setShapes([]);
      setLines(EMPTY_GRID);
      setScale(0.5);
    }
  }, [open, initialMode, cropInitialRegion, outpaintInitialPrompt]);

  if (!open) return null;

  const enabled = (m: EditorMode): boolean => {
    switch (m) {
      case 'preview':
        return true;
      case 'crop':
        return Boolean(onCropCommit);
      case 'outpaint':
        return Boolean(onOutpaintCommit);
      case 'mask':
        return Boolean(onMaskCommit);
      case 'brush':
        return Boolean(onBrushCommit);
      case 'resize':
        return Boolean(onResizeCommit);
      case 'split':
        return Boolean(onSplitCommit);
    }
  };

  const applyDisabled = (): boolean => {
    if (committing) return true;
    if (mode === 'mask') return !hasMaskContent(strokes);
    if (mode === 'outpaint') return !hasExtension(padding);
    if (mode === 'brush') return shapes.length === 0;
    if (mode === 'split') return lines.xs.length === 0 && lines.ys.length === 0;
    return false;
  };

  const apply = () => {
    if (mode === 'crop') onCropCommit?.(region);
    else if (mode === 'outpaint') onOutpaintCommit?.(padding, outpaintPrompt);
    else if (mode === 'mask')
      onMaskCommit?.(strokes, naturalSize ?? FALLBACK_MASK_SIZE);
    else if (mode === 'brush') onBrushCommit?.(shapes);
    else if (mode === 'resize') onResizeCommit?.(scale);
    else if (mode === 'split') onSplitCommit?.(lines);
  };

  const meta = MODE_META.find((m) => m.mode === mode)!;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div
        data-testid="unified-image-editor"
        className="flex h-[92vh] w-[94vw] max-w-6xl flex-col rounded-2xl bg-canvas-card p-4 shadow-2xl"
      >
      {/* Header: centered mode tab bar (IC image-edit-mode) + download/close
          floated right — the IC card puts tabs top-center. */}
      <div className="relative mb-3 flex items-center justify-center gap-2">
        <div className="flex items-center gap-1 rounded-xl border border-canvas-line bg-canvas-bg p-1">
          {MODE_META.filter((m) => enabled(m.mode)).map((m) => (
            <button
              key={m.mode}
              type="button"
              data-testid={`editor-tab-${m.mode}`}
              onClick={() => setMode(m.mode)}
              disabled={committing}
              className={`nodrag flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-semibold ${
                mode === m.mode
                  ? 'bg-canvas-strong text-canvas-card'
                  : 'text-canvas-text hover:bg-canvas-bg'
              }`}
            >
              {m.icon}
              {m.label}
            </button>
          ))}
        </div>
        <div className="absolute right-0 flex items-center gap-1.5">
          <a
            data-testid="editor-download"
            href={mediaSrc(src)}
            download
            aria-label="Download image"
            className="nodrag flex h-8 w-8 items-center justify-center rounded-lg border border-canvas-line text-canvas-text hover:bg-canvas-bg"
          >
            <Download size={14} />
          </a>
          <button
            type="button"
            aria-label="Close editor"
            onClick={onClose}
            disabled={committing}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-canvas-line text-canvas-text hover:bg-canvas-bg"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Mode toolbars. */}
      {mode === 'crop' && (
        <div className="mb-2 flex items-center gap-1.5 text-xs text-canvas-muted">
          <span>Aspect</span>
          {[
            { label: 'Free', value: null },
            { label: '1:1', value: 1 },
            { label: '4:3', value: 4 / 3 },
            { label: '16:9', value: 16 / 9 },
            { label: '3:4', value: 3 / 4 },
            { label: '9:16', value: 9 / 16 },
            { label: '3:2', value: 3 / 2 },
            { label: '2:3', value: 2 / 3 },
          ].map((preset) => {
            const active = preset.value === cropAspect;
            return (
              <button
                key={preset.label}
                type="button"
                data-testid={`editor-crop-aspect-${preset.label.toLowerCase().replace(':', '-')}`}
                onClick={() => {
                  setCropAspect(preset.value);
                  if (preset.value !== null)
                    setRegion(snapRegionToAspect(region, preset.value));
                }}
                className={`nodrag rounded-lg px-2 py-0.5 ${
                  active
                    ? 'bg-canvas-strong text-canvas-card'
                    : 'border border-canvas-line text-canvas-text'
                }`}
              >
                {preset.label}
              </button>
            );
          })}
        </div>
      )}
      {mode === 'mask' && (
        <div className="mb-2 flex items-center gap-2 text-xs text-canvas-muted">
          <span>Brush</span>
          {BRUSH_SIZES.map((b) => (
            <button
              key={b.value}
              type="button"
              onClick={() => setMaskSize(b.value)}
              className={`nodrag rounded-lg px-2 py-0.5 ${
                maskSize === b.value
                  ? 'bg-canvas-strong text-canvas-card'
                  : 'border border-canvas-line text-canvas-text'
              }`}
            >
              {b.label}
            </button>
          ))}
          <button
            type="button"
            data-testid="mask-undo"
            disabled={strokes.length === 0}
            onClick={() => {
              setStrokeRedo((r) => [...r, strokes]);
              setStrokes(strokes.slice(0, -1));
            }}
            className="nodrag rounded-lg border border-canvas-line px-2 py-0.5 text-canvas-text disabled:opacity-40"
          >
            Undo
          </button>
          <button
            type="button"
            data-testid="mask-redo"
            disabled={strokeRedo.length === 0}
            onClick={() => {
              const prev = strokeRedo[strokeRedo.length - 1];
              setStrokeRedo(strokeRedo.slice(0, -1));
              setStrokes(prev);
            }}
            className="nodrag rounded-lg border border-canvas-line px-2 py-0.5 text-canvas-text disabled:opacity-40"
          >
            Redo
          </button>
          <button
            type="button"
            onClick={() => {
              setStrokeRedo((r) => [...r, strokes]);
              setStrokes([]);
            }}
            className="nodrag rounded-lg border border-canvas-line px-2 py-0.5 text-canvas-text"
          >
            Clear
          </button>
          <span className="ml-2">Painted areas will be cut out</span>
        </div>
      )}
      {mode === 'brush' && (
        <div className="mb-2 flex items-center gap-2 text-xs text-canvas-muted">
          {PAINT_TOOLS.map((t) => (
            <button
              key={t.tool}
              type="button"
              data-testid={`paint-tool-${t.tool}`}
              onClick={() => setPaintTool(t.tool)}
              className={`nodrag rounded-lg px-2 py-0.5 ${
                paintTool === t.tool
                  ? 'bg-canvas-strong text-canvas-card'
                  : 'border border-canvas-line text-canvas-text'
              }`}
            >
              {t.label}
            </button>
          ))}
          <label className="ml-2 flex items-center gap-1">
            Color
            <input
              type="color"
              value={paintColor}
              onChange={(e) => setPaintColor(e.target.value)}
              aria-label="Brush color"
              className="h-5 w-8 cursor-pointer border-0 bg-transparent p-0"
            />
          </label>
          <label className="flex items-center gap-1">
            Size
            <input
              type="range"
              min={2}
              max={80}
              value={paintSize}
              onChange={(e) => setPaintSize(Number(e.target.value))}
              aria-label="Brush size"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              setShapeRedo((r) => [...r, shapes]);
              setShapes(shapes.slice(0, -1));
            }}
            disabled={shapes.length === 0}
            className="nodrag rounded-lg border border-canvas-line px-2 py-0.5 text-canvas-text disabled:opacity-40"
          >
            Undo
          </button>
          <button
            type="button"
            data-testid="brush-redo"
            disabled={shapeRedo.length === 0}
            onClick={() => {
              const prev = shapeRedo[shapeRedo.length - 1];
              setShapeRedo(shapeRedo.slice(0, -1));
              setShapes(prev);
            }}
            className="nodrag rounded-lg border border-canvas-line px-2 py-0.5 text-canvas-text disabled:opacity-40"
          >
            Redo
          </button>
          <button
            type="button"
            onClick={() => setShapes([])}
            disabled={shapes.length === 0}
            className="nodrag rounded-lg border border-canvas-line px-2 py-0.5 text-canvas-text disabled:opacity-40"
          >
            Clear
          </button>
        </div>
      )}
      {mode === 'split' && (
        <div className="mb-2 flex items-center gap-1.5 text-xs text-canvas-muted">
          <span>Presets</span>
          {[
            [1, 2],
            [2, 1],
            [2, 2],
            [2, 3],
            [3, 2],
            [3, 3],
          ].map(([r, c]) => (
            <button
              key={`${r}x${c}`}
              type="button"
              data-testid={`grid-preset-${r}x${c}`}
              onClick={() => setLines(presetGrid(r, c))}
              className="nodrag rounded-lg border border-canvas-line px-2 py-0.5 text-canvas-text"
            >
              {r}×{c}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setLines(EMPTY_GRID)}
            className="nodrag rounded-lg border border-canvas-line px-2 py-0.5 text-canvas-text"
          >
            Clear
          </button>
        </div>
      )}
      {mode === 'resize' && (
        <div className="mb-2 flex items-center gap-2 text-xs text-canvas-muted">
          <span>Scale</span>
          <input
            type="range"
            min={0.05}
            max={1}
            step={0.05}
            value={scale}
            onChange={(e) => setScale(Number(e.target.value))}
            aria-label="Resize scale"
          />
          <span className="font-semibold text-canvas-text">{scale.toFixed(2)}×</span>
          {naturalSize && (
            <span data-testid="resize-resolution">
              {Math.round(naturalSize.width * scale)} x{' '}
              {Math.round(naturalSize.height * scale)}
            </span>
          )}
        </div>
      )}

      {/* Stage. */}
      <div
        data-testid="editor-stage"
        onWheel={(e) => {
          // IC image-edit zoom: every mode scales (crop boxes and brushes
          // keep working — pointer math follows getBoundingClientRect, which
          // tracks the transform). Clamped 20%–400%.
          setZoom((z) =>
            Math.min(400, Math.max(20, Math.round(z * (e.deltaY < 0 ? 1.1 : 1 / 1.1)))),
          );
        }}
        className="relative flex min-h-0 flex-1 items-center justify-center overflow-auto rounded-xl border border-canvas-line bg-canvas-bg/60 p-3"
      >
        {mode === 'preview' && (
          <img
            src={mediaSrc(src)}
            alt={alt}
            className="max-h-full max-w-full object-contain"
            style={zoom !== 100 ? { transform: `scale(${zoom / 100})` } : undefined}
            onLoad={(e) =>
              setNaturalSize({
                width: e.currentTarget.naturalWidth,
                height: e.currentTarget.naturalHeight,
              })
            }
          />
        )}
        {mode === 'crop' && (
          <div style={zoom !== 100 ? { transform: `scale(${zoom / 100})` } : undefined}>
            <CropTool src={mediaSrc(src)} alt={alt} value={region} onChange={setRegion} />
          </div>
        )}
        {mode === 'outpaint' && (
          <div className="flex flex-col items-center gap-2">
            <OutpaintTool
              src={mediaSrc(src)}
              alt={alt}
              value={padding}
              onChange={setPadding}
              onNaturalSize={setNaturalSize}
            />
            <input
              type="text"
              value={outpaintPrompt}
              onChange={(e) => setOutpaintPrompt(e.target.value)}
              placeholder="Describe what fills the extended area (optional)"
              aria-label="Outpaint prompt"
              className="nodrag w-96 rounded-lg border border-canvas-line bg-transparent px-2 py-1 text-xs text-canvas-text outline-none"
            />
          </div>
        )}
        {mode === 'mask' && (
          <div style={zoom !== 100 ? { transform: `scale(${zoom / 100})` } : undefined}>
          <MaskBrushTool
            src={mediaSrc(src)}
            alt={alt}
            value={strokes}
            onChange={setStrokes}
            tool={maskTool}
            brushSize={maskSize}
            onNaturalSize={setNaturalSize}
          />
          </div>
        )}
        {mode === 'brush' && (
          <div style={zoom !== 100 ? { transform: `scale(${zoom / 100})` } : undefined}>
          <PaintTool
            src={src}
            alt={alt}
            value={shapes}
            onChange={setShapes}
            tool={paintTool}
            color={paintColor}
            size={paintSize}
          />
          </div>
        )}
        {mode === 'resize' && (
          <img
            src={mediaSrc(src)}
            alt={alt}
            className="max-h-full max-w-full object-contain opacity-90"
            style={{ transform: `scale(${Math.max(scale, 0.2)})` }}
            onLoad={(e) =>
              setNaturalSize({
                width: e.currentTarget.naturalWidth,
                height: e.currentTarget.naturalHeight,
              })
            }
          />
        )}
        {mode === 'split' && (
          <div style={zoom !== 100 ? { transform: `scale(${zoom / 100})` } : undefined}>
          <GridSplitTool src={mediaSrc(src)} alt={alt} value={lines} onChange={setLines} />
          </div>
        )}
        <span
          data-testid="editor-zoom"
          title="Double-click to reset"
          onDoubleClick={() => setZoom(100)}
          className="absolute bottom-2 left-2 cursor-pointer rounded-md bg-canvas-card/90 px-1.5 py-0.5 text-[10px] font-semibold text-canvas-muted"
        >
          {zoom}%
        </span>
      </div>

      {/* Footer. */}
      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          data-testid="editor-cancel"
          onClick={onClose}
          disabled={committing}
          className="nodrag ml-auto rounded-full border border-canvas-line px-3 py-1 text-xs text-canvas-text"
        >
          Cancel
        </button>
        {meta.apply && (
          <button
            type="button"
            data-testid="editor-apply"
            onClick={apply}
            disabled={applyDisabled()}
            className="nodrag rounded-full border border-transparent bg-canvas-strong px-3 py-1 text-xs font-bold text-canvas-card disabled:opacity-40"
          >
            {committing ? 'Working…' : meta.apply}
          </button>
        )}
      </div>
      </div>
    </div>
  );
}
