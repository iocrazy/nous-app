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

import { snapRegionToAspect } from './cropMath';
import { CropTool } from './CropTool';
import { FULL_REGION, type CropRegion } from './types';

interface AspectPreset {
  label: string;
  /** null = free / no constraint. */
  value: number | null;
}

const ASPECT_PRESETS: ReadonlyArray<AspectPreset> = [
  { label: 'Free', value: null },
  { label: '1:1', value: 1 },
  { label: '4:3', value: 4 / 3 },
  { label: '16:9', value: 16 / 9 },
  { label: '3:4', value: 3 / 4 },
  { label: '9:16', value: 9 / 16 },
];

interface CropEditorModalProps {
  open: boolean;
  src: string;
  alt?: string;
  initialRegion?: CropRegion;
  onCommit(region: CropRegion): void;
  onCancel(): void;
  /** When true, the modal disables Commit / Cancel / X and shows a
   *  busy label on the Commit button. Parent uses this for any in-flight
   *  derive / persist call. */
  committing?: boolean;
}

export function CropEditorModal({
  open,
  src,
  alt = '',
  initialRegion,
  onCommit,
  onCancel,
  committing = false,
}: CropEditorModalProps) {
  const [region, setRegion] = useState<CropRegion>(
    initialRegion ?? FULL_REGION,
  );
  const [selectedAspect, setSelectedAspect] = useState<number | null>(null);

  // Reset the in-progress region every time the modal re-opens so a
  // previous session's drag doesn't bleed into a fresh edit.
  useEffect(() => {
    if (open) {
      setRegion(initialRegion ?? FULL_REGION);
      setSelectedAspect(null);
    }
  }, [open, initialRegion]);

  const handleAspectClick = useCallback(
    (aspect: number | null) => {
      setSelectedAspect(aspect);
      if (aspect !== null) {
        setRegion((current) => snapRegionToAspect(current, aspect));
      }
    },
    [],
  );

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
            disabled={committing}
            aria-label="Close crop editor"
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
          data-testid="crop-aspect-toolbar"
          role="group"
          aria-label="Aspect ratio"
          className="flex flex-wrap items-center gap-1"
        >
          {ASPECT_PRESETS.map((preset) => {
            const active = preset.value === selectedAspect;
            return (
              <button
                key={preset.label}
                type="button"
                data-testid={`crop-aspect-${preset.label.toLowerCase().replace(':', '-')}`}
                aria-pressed={active}
                onClick={() => handleAspectClick(preset.value)}
                className={`rounded border px-2 py-1 text-xs font-medium transition ${
                  active
                    ? 'border-indigo-500 bg-indigo-600 text-white'
                    : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700'
                }`}
              >
                {preset.label}
              </button>
            );
          })}
        </div>

        <div className="flex-1 overflow-auto">
          <CropTool src={src} alt={alt} value={region} onChange={setRegion} />
        </div>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            data-testid="crop-editor-cancel"
            onClick={onCancel}
            disabled={committing}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="crop-editor-commit"
            onClick={handleCommit}
            disabled={committing}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {committing ? 'Committing…' : 'Commit'}
          </button>
        </div>
      </div>
    </div>
  );
}
