import { useState, useCallback } from 'react';
import { Grid3x3, Check, X, Minus, Plus } from 'lucide-react';
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

export function SplitStoryboardToolEditor({ imageUrl, onConfirm, onCancel }: SplitStoryboardToolEditorProps) {
  const [rows, setRows] = useState(3);
  const [cols, setCols] = useState(3);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = useCallback(async () => {
    setProcessing(true);
    setError(null);
    try {
      const result = await canvasToolProcessor.process(
        NODE_TOOL_TYPES.splitStoryboard,
        imageUrl,
        { rows, cols, lineThickness: 0 },
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
  }, [imageUrl, rows, cols, onConfirm]);

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-text-dark">
        <Grid3x3 className="h-4 w-4" />
        <span>Split into Storyboard</span>
      </div>

      <div className="relative overflow-hidden rounded-lg border border-[rgba(255,255,255,0.1)] bg-bg-dark/60">
        <img src={imageUrl} alt="Split preview" className="block max-h-[300px] w-full object-contain" />
        {/* Grid overlay */}
        <div className="absolute inset-0 pointer-events-none">
          {Array.from({ length: cols - 1 }).map((_, i) => (
            <div
              key={`col-${i}`}
              className="absolute top-0 bottom-0 w-px bg-blue-400/60"
              style={{ left: `${((i + 1) / cols) * 100}%` }}
            />
          ))}
          {Array.from({ length: rows - 1 }).map((_, i) => (
            <div
              key={`row-${i}`}
              className="absolute left-0 right-0 h-px bg-blue-400/60"
              style={{ top: `${((i + 1) / rows) * 100}%` }}
            />
          ))}
        </div>
      </div>

      <div className="flex items-center gap-4">
        <StepperControl label="Rows" value={rows} min={1} max={9} onChange={setRows} />
        <StepperControl label="Cols" value={cols} min={1} max={9} onChange={setCols} />
        <div className="text-xs text-text-muted ml-auto">
          {rows * cols} frames
        </div>
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="flex justify-end gap-2">
        <UiButton size="sm" variant="ghost" onClick={onCancel}>
          <X className="h-3.5 w-3.5" />
          Cancel
        </UiButton>
        <UiButton size="sm" variant="primary" disabled={processing} onClick={handleConfirm}>
          <Check className="h-3.5 w-3.5" />
          {processing ? 'Splitting...' : 'Split'}
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
      <button
        type="button"
        onClick={() => onChange(Math.max(min, value - 1))}
        className="flex h-5 w-5 items-center justify-center rounded bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]"
      >
        <Minus className="h-3 w-3" />
      </button>
      <span className="min-w-[18px] text-center text-xs font-semibold text-text-dark">{value}</span>
      <button
        type="button"
        onClick={() => onChange(Math.min(max, value + 1))}
        className="flex h-5 w-5 items-center justify-center rounded bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]"
      >
        <Plus className="h-3 w-3" />
      </button>
    </div>
  );
}
