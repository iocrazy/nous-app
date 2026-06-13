/**
 * Modal shell around `OutpaintTool` (Phase 3 Day 14).
 *
 *   open=true                   → backdrop + dialog with the tool inside.
 *   onCommit(padding, prompt)   → caller derives the extension.
 *   onCancel()                  → caller discards.
 *
 * Shows aspect presets (16:9 / 9:16 / 1:1 / 4:3), a live resolution
 * readout (source → target, from the image's natural size), and a
 * prompt field auto-filled from `initialPrompt`. The prompt is
 * collected for the future AI fill — the current fill extends with a
 * blurred background, and the helper text says so.
 *
 * Extend is disabled until at least one side has padding.
 */

import { useCallback, useEffect, useState } from 'react';

import { OutpaintTool } from './OutpaintTool';
import {
  ASPECT_PRESETS,
  ZERO_PADDING,
  hasExtension,
  paddingForAspect,
  targetResolution,
  type OutpaintPadding,
} from './outpaintMath';

interface OutpaintEditorModalProps {
  open: boolean;
  src: string;
  alt?: string;
  /** Auto-fills the prompt field (e.g. the node's caption). */
  initialPrompt?: string;
  onCommit(padding: OutpaintPadding, prompt: string): void;
  onCancel(): void;
  /** Disables every control and shows a busy label on Extend while
   *  the derive call is in flight. */
  committing?: boolean;
}

export function OutpaintEditorModal({
  open,
  src,
  alt = '',
  initialPrompt = '',
  onCommit,
  onCancel,
  committing = false,
}: OutpaintEditorModalProps) {
  const [padding, setPadding] = useState<OutpaintPadding>(ZERO_PADDING);
  const [prompt, setPrompt] = useState(initialPrompt);
  const [naturalSize, setNaturalSize] = useState<{
    width: number;
    height: number;
  } | null>(null);

  // Reset in-progress state every time the modal re-opens.
  useEffect(() => {
    if (open) {
      setPadding(ZERO_PADDING);
      setPrompt(initialPrompt);
    }
  }, [open, initialPrompt]);

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
    onCommit(padding, prompt.trim());
  }, [onCommit, padding, prompt]);

  if (!open) return null;

  const extendReady = hasExtension(padding);
  const target = naturalSize ? targetResolution(padding, naturalSize) : null;

  return (
    <div
      data-testid="outpaint-editor-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="outpaint-editor-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={handleBackdropClick}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[90vh] max-w-[92vw] flex-col gap-4 overflow-auto rounded-lg bg-white p-5 shadow-2xl dark:bg-slate-900"
      >
        <div className="flex items-center justify-between">
          <h2
            id="outpaint-editor-title"
            className="text-base font-semibold text-slate-900 dark:text-slate-100"
          >
            Extend canvas
          </h2>
          <button
            type="button"
            data-testid="outpaint-editor-close"
            onClick={onCancel}
            disabled={committing}
            aria-label="Close outpaint editor"
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
          data-testid="outpaint-toolbar"
          role="group"
          aria-label="Aspect presets"
          className="flex flex-wrap items-center gap-1"
        >
          {ASPECT_PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              data-testid={`outpaint-aspect-${preset.label.replace(':', '-')}`}
              disabled={committing || !naturalSize}
              onClick={() => {
                if (!naturalSize) return;
                setPadding(paddingForAspect(preset.value, naturalSize));
              }}
              className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              {preset.label}
            </button>
          ))}
          <button
            type="button"
            data-testid="outpaint-clear"
            disabled={committing || !extendReady}
            onClick={() => setPadding(ZERO_PADDING)}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            Reset
          </button>
          <span
            data-testid="outpaint-resolution"
            className="ml-auto text-xs text-slate-500 dark:text-slate-400"
          >
            {naturalSize && target
              ? `${naturalSize.width} × ${naturalSize.height} → ${target.width} × ${target.height}`
              : 'Resolution pending image load'}
          </span>
        </div>

        <div className="flex-1 overflow-auto">
          <OutpaintTool
            src={src}
            alt={alt}
            value={padding}
            onChange={setPadding}
            onNaturalSize={setNaturalSize}
          />
        </div>

        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-slate-600 dark:text-slate-300">
            Prompt
          </span>
          <textarea
            data-testid="outpaint-prompt"
            value={prompt}
            disabled={committing}
            onChange={(event) => setPrompt(event.target.value)}
            rows={2}
            maxLength={2000}
            placeholder="Describe what should fill the extended area"
            className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-800 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
          />
          <span className="text-[11px] text-slate-400">
            Used by AI fill once a generation provider is connected. The
            current version extends with a blurred background.
          </span>
        </label>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            data-testid="outpaint-editor-cancel"
            onClick={onCancel}
            disabled={committing}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="outpaint-editor-commit"
            onClick={handleCommit}
            disabled={committing || !extendReady}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {committing ? 'Extending…' : 'Extend'}
          </button>
        </div>
      </div>
    </div>
  );
}
