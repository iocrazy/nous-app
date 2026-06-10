/**
 * Modal shell around `MaskBrushTool` (Phase 3 Day 11).
 *
 *   open=true                  → backdrop + dialog with the painter inside.
 *   onCommit(strokes, size)    → caller rasterizes + derives the cutout.
 *   onCancel()                 → caller discards.
 *
 * The modal owns the in-progress strokes and tool state, so Cancel is
 * a true no-op. `size` passed to onCommit is the image's natural
 * pixel size when known (reported by the painter on image load) —
 * the caller uses it to rasterize the mask at source resolution.
 *
 * Cut Out is disabled until at least one brush stroke exists — an
 * eraser-only mask keeps nothing and the endpoint rejects it.
 */

import { useCallback, useEffect, useState } from 'react';

import { MaskBrushTool } from './MaskBrushTool';
import {
  BRUSH_SIZES,
  hasMaskContent,
  type MaskStroke,
  type MaskTool,
} from './maskMath';

/** Fallback raster size when the image never reported natural
 *  dimensions (e.g. load event lost) — backend rescales anyway. */
export const FALLBACK_MASK_SIZE = { width: 1024, height: 1024 };

interface MaskEditorModalProps {
  open: boolean;
  src: string;
  alt?: string;
  onCommit(
    strokes: MaskStroke[],
    size: { width: number; height: number },
  ): void;
  onCancel(): void;
  /** Disables every control and shows a busy label on Cut Out while
   *  the derive call is in flight. */
  committing?: boolean;
}

export function MaskEditorModal({
  open,
  src,
  alt = '',
  onCommit,
  onCancel,
  committing = false,
}: MaskEditorModalProps) {
  const [strokes, setStrokes] = useState<MaskStroke[]>([]);
  const [tool, setTool] = useState<MaskTool>('brush');
  const [brushSize, setBrushSize] = useState<number>(BRUSH_SIZES[1].value);
  const [naturalSize, setNaturalSize] = useState<{
    width: number;
    height: number;
  } | null>(null);

  // Reset in-progress state every time the modal re-opens.
  useEffect(() => {
    if (open) {
      setStrokes([]);
      setTool('brush');
      setBrushSize(BRUSH_SIZES[1].value);
    }
  }, [open]);

  // Esc → cancel (disabled while committing).
  useEffect(() => {
    if (!open || committing) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel, committing]);

  const handleBackdropClick = useCallback(() => {
    if (committing) return;
    onCancel();
  }, [onCancel, committing]);

  const handleCommit = useCallback(() => {
    onCommit(strokes, naturalSize ?? FALLBACK_MASK_SIZE);
  }, [onCommit, strokes, naturalSize]);

  if (!open) return null;

  const maskReady = hasMaskContent(strokes);

  const toolButton = (value: MaskTool, label: string) => (
    <button
      type="button"
      data-testid={`mask-tool-${value}`}
      aria-pressed={tool === value}
      disabled={committing}
      onClick={() => setTool(value)}
      className={`rounded border px-2 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
        tool === value
          ? 'border-indigo-500 bg-indigo-600 text-white'
          : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700'
      }`}
    >
      {label}
    </button>
  );

  return (
    <div
      data-testid="mask-editor-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="mask-editor-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={handleBackdropClick}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[90vh] max-w-[92vw] flex-col gap-4 rounded-lg bg-white p-5 shadow-2xl dark:bg-slate-900"
      >
        <div className="flex items-center justify-between">
          <h2
            id="mask-editor-title"
            className="text-base font-semibold text-slate-900 dark:text-slate-100"
          >
            Cut out region
          </h2>
          <button
            type="button"
            data-testid="mask-editor-close"
            onClick={onCancel}
            disabled={committing}
            aria-label="Close mask editor"
            className="rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div
          data-testid="mask-toolbar"
          role="group"
          aria-label="Mask tools"
          className="flex flex-wrap items-center gap-1"
        >
          {toolButton('brush', 'Brush')}
          {toolButton('eraser', 'Eraser')}
          <span className="mx-1 h-4 w-px bg-slate-300 dark:bg-slate-600" />
          {BRUSH_SIZES.map((preset) => {
            const active = preset.value === brushSize;
            return (
              <button
                key={preset.label}
                type="button"
                data-testid={`mask-size-${preset.label.toLowerCase()}`}
                aria-pressed={active}
                disabled={committing}
                onClick={() => setBrushSize(preset.value)}
                className={`rounded border px-2 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                  active
                    ? 'border-indigo-500 bg-indigo-600 text-white'
                    : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700'
                }`}
              >
                {preset.label}
              </button>
            );
          })}
          <span className="mx-1 h-4 w-px bg-slate-300 dark:bg-slate-600" />
          <button
            type="button"
            data-testid="mask-clear"
            disabled={committing || strokes.length === 0}
            onClick={() => setStrokes([])}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            Clear
          </button>
        </div>

        <div className="flex-1 overflow-auto">
          <MaskBrushTool
            src={src}
            alt={alt}
            value={strokes}
            onChange={setStrokes}
            tool={tool}
            brushSize={brushSize}
            onNaturalSize={setNaturalSize}
          />
        </div>

        <p className="text-xs text-slate-500 dark:text-slate-400">
          Paint the area to keep. Everything else becomes transparent.
        </p>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            data-testid="mask-editor-cancel"
            onClick={onCancel}
            disabled={committing}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="mask-editor-commit"
            onClick={handleCommit}
            disabled={committing || !maskReady}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {committing ? 'Cutting out…' : 'Cut Out'}
          </button>
        </div>
      </div>
    </div>
  );
}
