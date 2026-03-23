import { useState, useCallback, useMemo } from 'react';
import { Grid3x3, Check, X, Minus, Plus, Eye } from 'lucide-react';
import { UiButton } from '../../../components/ui';
import { canvasToolProcessor } from '../application/canvasServices';
import { NODE_TOOL_TYPES } from '../domain/canvasNodes';

interface SplitStoryboardToolEditorProps {
  imageUrl: string;
  onConfirm: (result: {
    rows: number;
    cols: number;
    frames: Array<{
      id: string;
      imageUrl: string | null;
      previewImageUrl?: string | null;
      aspectRatio?: string;
      note: string;
      order: number;
    }>;
    frameAspectRatio?: string;
  }) => void;
  onCancel: () => void;
}

const LINE_THICKNESS_OPTIONS = [
  { label: 'None', value: 0 },
  { label: 'Thin', value: 1 },
  { label: 'Medium', value: 2 },
  { label: 'Thick', value: 4 },
] as const;

const SUGGESTED_GRIDS = [
  { label: '2x2', rows: 2, cols: 2 },
  { label: '2x3', rows: 2, cols: 3 },
  { label: '3x3', rows: 3, cols: 3 },
  { label: '3x4', rows: 3, cols: 4 },
  { label: '4x4', rows: 4, cols: 4 },
] as const;

export function SplitStoryboardToolEditor({ imageUrl, onConfirm, onCancel }: SplitStoryboardToolEditorProps) {
  const [rows, setRows] = useState(3);
  const [cols, setCols] = useState(3);
  const [lineThickness, setLineThickness] = useState(0);
  const [showFrameNumbers, setShowFrameNumbers] = useState(true);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hoveredCell, setHoveredCell] = useState<number | null>(null);
  const [excludedCells, setExcludedCells] = useState<Set<number>>(new Set());

  const totalFrames = rows * cols;

  const gridLines = useMemo(() => {
    const verticals = Array.from({ length: cols - 1 }, (_, i) => ({
      key: `col-${i}`,
      left: `${((i + 1) / cols) * 100}%`,
      width: lineThickness > 0 ? `${Math.max(1, lineThickness)}px` : '1px',
      opacity: lineThickness > 0 ? 0.8 : 0.6,
    }));
    const horizontals = Array.from({ length: rows - 1 }, (_, i) => ({
      key: `row-${i}`,
      top: `${((i + 1) / rows) * 100}%`,
      height: lineThickness > 0 ? `${Math.max(1, lineThickness)}px` : '1px',
      opacity: lineThickness > 0 ? 0.8 : 0.6,
    }));
    return { verticals, horizontals };
  }, [rows, cols, lineThickness]);

  const frameLabels = useMemo(() => {
    if (!showFrameNumbers) return [];
    return Array.from({ length: totalFrames }, (_, i) => {
      const row = Math.floor(i / cols);
      const col = i % cols;
      return {
        key: `label-${i}`,
        label: `S${i + 1}`,
        left: `${(col / cols) * 100 + (1 / cols) * 50}%`,
        top: `${(row / rows) * 100 + (1 / rows) * 50}%`,
      };
    });
  }, [showFrameNumbers, totalFrames, rows, cols]);

  const handleConfirm = useCallback(async () => {
    setProcessing(true);
    setError(null);
    try {
      const result = await canvasToolProcessor.process(
        NODE_TOOL_TYPES.splitStoryboard,
        imageUrl,
        { rows, cols, lineThickness },
      );

      if (result.storyboardFrames) {
        onConfirm({
          rows: result.rows ?? rows,
          cols: result.cols ?? cols,
          frames: result.storyboardFrames,
          frameAspectRatio: result.frameAspectRatio,
        });
      } else {
        setError('Split produced no frames');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Split failed');
    } finally {
      setProcessing(false);
    }
  }, [imageUrl, rows, cols, lineThickness, onConfirm]);

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-text-dark">
        <Grid3x3 className="h-4 w-4" />
        <span>Split into Storyboard</span>
      </div>

      <div className="relative overflow-hidden rounded-lg border border-[rgba(255,255,255,0.1)] bg-bg-dark/60">
        <img src={imageUrl} alt="Split preview" className="block max-h-[300px] w-full object-contain" draggable={false} />
        {/* Grid overlay */}
        <div className="absolute inset-0 pointer-events-none">
          {gridLines.verticals.map((line) => (
            <div
              key={line.key}
              className="absolute top-0 bottom-0 bg-indigo-400"
              style={{ left: line.left, width: line.width, opacity: line.opacity }}
            />
          ))}
          {gridLines.horizontals.map((line) => (
            <div
              key={line.key}
              className="absolute left-0 right-0 bg-indigo-400"
              style={{ top: line.top, height: line.height, opacity: line.opacity }}
            />
          ))}
          {/* Frame number labels */}
          {frameLabels.map((label) => (
            <div
              key={label.key}
              className="absolute flex items-center justify-center"
              style={{
                left: label.left,
                top: label.top,
                transform: 'translate(-50%, -50%)',
              }}
            >
              <span className="rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                {label.label}
              </span>
            </div>
          ))}
          {/* Cell interaction zones */}
          {Array.from({ length: totalFrames }, (_, i) => {
            const row = Math.floor(i / cols);
            const col = i % cols;
            const isExcluded = excludedCells.has(i);
            const isHovered = hoveredCell === i;
            return (
              <div
                key={`cell-${i}`}
                className={`absolute cursor-pointer transition-colors ${
                  isExcluded
                    ? 'bg-red-500/30'
                    : isHovered
                      ? 'bg-indigo-400/15'
                      : ''
                }`}
                style={{
                  left: `${(col / cols) * 100}%`,
                  top: `${(row / rows) * 100}%`,
                  width: `${(1 / cols) * 100}%`,
                  height: `${(1 / rows) * 100}%`,
                  pointerEvents: 'auto',
                }}
                onMouseEnter={() => setHoveredCell(i)}
                onMouseLeave={() => setHoveredCell(null)}
                onClick={(e) => {
                  e.stopPropagation();
                  setExcludedCells((prev) => {
                    const next = new Set(prev);
                    if (next.has(i)) {
                      next.delete(i);
                    } else {
                      // Don't exclude all cells
                      if (next.size < totalFrames - 1) {
                        next.add(i);
                      }
                    }
                    return next;
                  });
                }}
                title={isExcluded ? `Click to include S${i + 1}` : `Click to exclude S${i + 1}`}
              >
                {isExcluded && (
                  <div className="flex h-full items-center justify-center">
                    <X className="h-4 w-4 text-red-300/80" />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Quick grid presets */}
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-text-muted">Grid:</span>
        {SUGGESTED_GRIDS.map((grid) => (
          <button
            key={grid.label}
            type="button"
            onClick={() => { setRows(grid.rows); setCols(grid.cols); setExcludedCells(new Set()); }}
            className={`rounded-full px-2 py-0.5 text-[10px] transition-colors ${
              rows === grid.rows && cols === grid.cols
                ? 'bg-indigo-600 text-white'
                : 'bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]'
            }`}
          >
            {grid.label}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-4">
        <StepperControl label="Rows" value={rows} min={1} max={9} onChange={(v) => { setRows(v); setExcludedCells(new Set()); }} />
        <StepperControl label="Cols" value={cols} min={1} max={9} onChange={(v) => { setCols(v); setExcludedCells(new Set()); }} />
        <div className="flex flex-col items-end gap-0.5 ml-auto">
          <span className="text-xs text-text-muted">
            {totalFrames - excludedCells.size} / {totalFrames} frames
          </span>
          {excludedCells.size > 0 && (
            <span className="text-[10px] text-amber-400">{excludedCells.size} excluded</span>
          )}
        </div>
      </div>

      {/* Cell hover preview */}
      {hoveredCell !== null && (
        <div className="flex items-center gap-1.5 text-[10px] text-text-muted">
          <Eye className="h-3 w-3" />
          <span>
            Cell S{hoveredCell + 1} — Row {Math.floor(hoveredCell / cols) + 1}, Col {(hoveredCell % cols) + 1}
            {excludedCells.has(hoveredCell) ? ' (excluded)' : ''}
          </span>
        </div>
      )}

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-text-muted">Line:</span>
          <div className="flex gap-1">
            {LINE_THICKNESS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setLineThickness(opt.value)}
                className={`rounded-full px-2 py-0.5 text-[10px] transition-colors ${
                  lineThickness === opt.value
                    ? 'bg-indigo-600 text-white'
                    : 'bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <label className="ml-auto flex items-center gap-1.5 text-xs text-text-muted cursor-pointer">
          <input
            type="checkbox"
            checked={showFrameNumbers}
            onChange={(e) => setShowFrameNumbers(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-zinc-600 bg-zinc-800 text-indigo-500 focus:ring-indigo-500/30"
          />
          Numbers
        </label>
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="flex justify-end gap-2">
        <UiButton size="sm" variant="ghost" onClick={onCancel}>
          <X className="h-3.5 w-3.5" /> Cancel
        </UiButton>
        <UiButton size="sm" variant="primary" disabled={processing} onClick={handleConfirm}>
          <Check className="h-3.5 w-3.5" /> {processing ? 'Splitting...' : 'Split'}
        </UiButton>
      </div>
    </div>
  );
}

function StepperControl({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-text-muted">{label}</span>
      <button type="button" onClick={() => onChange(Math.max(min, value - 1))}
        className="flex h-5 w-5 items-center justify-center rounded bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]"
      ><Minus className="h-3 w-3" /></button>
      <span className="min-w-[18px] text-center text-xs font-semibold text-text-dark">{value}</span>
      <button type="button" onClick={() => onChange(Math.min(max, value + 1))}
        className="flex h-5 w-5 items-center justify-center rounded bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]"
      ><Plus className="h-3 w-3" /></button>
    </div>
  );
}
