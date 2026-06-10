/**
 * Modal shell around `GridSplitTool` (Phase 3 Day 8).
 *
 *   open=true        → render a backdrop + dialog with the editor inside.
 *   onCommit(lines)  → caller derives the tiles.
 *   onCancel()       → caller discards.
 *
 * The modal owns its own in-progress lines (so Cancel really is a
 * no-op, regardless of how much editing happened). The caller only
 * sees the final lines via `onCommit`. Commit is disabled until at
 * least one split line exists — the derive endpoint rejects an empty
 * grid.
 *
 * Esc + backdrop click + the explicit Cancel button all route through
 * `onCancel`. Commit is the only escape hatch that ships lines.
 */

import { useCallback, useEffect, useState } from 'react';

import { GridSplitTool } from './GridSplitTool';
import {
  EMPTY_GRID,
  addLineAtLargestGap,
  gridShape,
  hasSplit,
  presetGrid,
  tileCount,
  type GridLines,
} from './gridMath';

interface GridPreset {
  label: string;
  rows: number;
  cols: number;
}

const GRID_PRESETS: ReadonlyArray<GridPreset> = [
  { label: '2×2', rows: 2, cols: 2 },
  { label: '3×3', rows: 3, cols: 3 },
  { label: '4×4', rows: 4, cols: 4 },
];

interface GridSplitEditorModalProps {
  open: boolean;
  src: string;
  alt?: string;
  initialLines?: GridLines;
  onCommit(lines: GridLines): void;
  onCancel(): void;
  /** When true, the modal disables all controls and shows a busy
   *  label on the Commit button. Parent uses this for the in-flight
   *  derive call. */
  committing?: boolean;
}

export function GridSplitEditorModal({
  open,
  src,
  alt = '',
  initialLines,
  onCommit,
  onCancel,
  committing = false,
}: GridSplitEditorModalProps) {
  const [lines, setLines] = useState<GridLines>(initialLines ?? EMPTY_GRID);

  // Reset the in-progress lines every time the modal re-opens so a
  // previous session's edits don't bleed into a fresh one.
  useEffect(() => {
    if (open) {
      setLines(initialLines ?? EMPTY_GRID);
    }
  }, [open, initialLines]);

  // Esc → cancel. (Disabled while a commit is in-flight so the user
  // can't bail mid-network.)
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
    onCommit(lines);
  }, [onCommit, lines]);

  if (!open) return null;

  const { rows, cols } = gridShape(lines);
  const splitReady = hasSplit(lines);

  return (
    <div
      data-testid="grid-editor-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="grid-editor-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={handleBackdropClick}
    >
      <div
        // Stop the backdrop handler from firing when the user interacts
        // with the dialog body itself.
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[90vh] max-w-[92vw] flex-col gap-4 rounded-lg bg-white p-5 shadow-2xl dark:bg-slate-900"
      >
        <div className="flex items-center justify-between">
          <h2
            id="grid-editor-title"
            className="text-base font-semibold text-slate-900 dark:text-slate-100"
          >
            Split image
          </h2>
          <button
            type="button"
            data-testid="grid-editor-close"
            onClick={onCancel}
            disabled={committing}
            aria-label="Close grid editor"
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
          data-testid="grid-toolbar"
          role="group"
          aria-label="Grid presets"
          className="flex flex-wrap items-center gap-1"
        >
          {GRID_PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              data-testid={`grid-preset-${preset.rows}x${preset.cols}`}
              disabled={committing}
              onClick={() => setLines(presetGrid(preset.rows, preset.cols))}
              className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              {preset.label}
            </button>
          ))}
          <span className="mx-1 h-4 w-px bg-slate-300 dark:bg-slate-600" />
          <button
            type="button"
            data-testid="grid-add-vertical"
            disabled={committing}
            onClick={() => setLines((current) => addLineAtLargestGap(current, 'x'))}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            + Vertical
          </button>
          <button
            type="button"
            data-testid="grid-add-horizontal"
            disabled={committing}
            onClick={() => setLines((current) => addLineAtLargestGap(current, 'y'))}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            + Horizontal
          </button>
          <button
            type="button"
            data-testid="grid-clear"
            disabled={committing || !splitReady}
            onClick={() => setLines(EMPTY_GRID)}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            Clear
          </button>
          <span
            data-testid="grid-tile-count"
            className="ml-auto text-xs text-slate-500 dark:text-slate-400"
          >
            {tileCount(lines)} tiles ({rows} × {cols})
          </span>
        </div>

        <div className="flex-1 overflow-auto">
          <GridSplitTool src={src} alt={alt} value={lines} onChange={setLines} />
        </div>

        <p className="text-xs text-slate-500 dark:text-slate-400">
          Drag a line to move it. Double-click a line to remove it.
        </p>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            data-testid="grid-editor-cancel"
            onClick={onCancel}
            disabled={committing}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="grid-editor-commit"
            onClick={handleCommit}
            disabled={committing || !splitReady}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {committing ? 'Splitting…' : 'Split'}
          </button>
        </div>
      </div>
    </div>
  );
}
