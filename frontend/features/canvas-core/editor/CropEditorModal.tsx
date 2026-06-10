/**
 * Modal shell around `CropTool` (Phase 3 Day 2).
 *
 *   open=true       → render a backdrop + dialog with the editor inside.
 *   onCommit(region) → caller persists the region.
 *   onCancel()      → caller discards.
 *
 * The modal owns its own in-progress region (so Cancel really is a
 * no-op, regardless of how much dragging happened). The caller only
 * sees the final region via `onCommit`.
 *
 * Esc + backdrop click + the explicit Cancel button all route through
 * `onCancel`. Commit is the only escape hatch that ships a region.
 */

import { useCallback, useEffect, useState } from 'react';

import { CropTool } from './CropTool';
import { FULL_REGION, type CropRegion } from './types';

interface CropEditorModalProps {
  open: boolean;
  src: string;
  alt?: string;
  initialRegion?: CropRegion;
  onCommit(region: CropRegion): void;
  onCancel(): void;
}

export function CropEditorModal({
  open,
  src,
  alt = '',
  initialRegion,
  onCommit,
  onCancel,
}: CropEditorModalProps) {
  const [region, setRegion] = useState<CropRegion>(
    initialRegion ?? FULL_REGION,
  );

  // Reset the in-progress region every time the modal re-opens so a
  // previous session's drag doesn't bleed into a fresh edit.
  useEffect(() => {
    if (open) setRegion(initialRegion ?? FULL_REGION);
  }, [open, initialRegion]);

  // Esc → cancel.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  const handleBackdropClick = useCallback(() => {
    onCancel();
  }, [onCancel]);

  const handleCommit = useCallback(() => {
    onCommit(region);
  }, [onCommit, region]);

  if (!open) return null;

  return (
    <div
      data-testid="crop-editor-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="crop-editor-title"
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
            id="crop-editor-title"
            className="text-base font-semibold text-slate-900 dark:text-slate-100"
          >
            Crop image
          </h2>
          <button
            type="button"
            data-testid="crop-editor-close"
            onClick={onCancel}
            aria-label="Close crop editor"
            className="rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
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

        <div className="flex-1 overflow-auto">
          <CropTool src={src} alt={alt} value={region} onChange={setRegion} />
        </div>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            data-testid="crop-editor-cancel"
            onClick={onCancel}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="crop-editor-commit"
            onClick={handleCommit}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
          >
            Commit
          </button>
        </div>
      </div>
    </div>
  );
}
