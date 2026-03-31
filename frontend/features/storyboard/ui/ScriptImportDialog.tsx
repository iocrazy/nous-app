import { useState, useCallback, useEffect } from 'react';
import { X, FileText, Loader2 } from 'lucide-react';
import { splitScript } from '../../../services/storyboardService';

const STYLE_OPTIONS = [
  { value: '', label: 'Default' },
  { value: 'realistic', label: 'Realistic' },
  { value: 'anime', label: 'Anime' },
  { value: 'comic', label: 'Comic' },
  { value: 'watercolor', label: 'Watercolor' },
  { value: 'sketch', label: 'Sketch' },
] as const;

interface ScriptImportDialogProps {
  projectId: string;
  isOpen: boolean;
  onClose: () => void;
}

type Status = 'idle' | 'submitting' | 'success' | 'error';

interface FormState {
  scriptText: string;
  targetFrames: string;
  style: string;
}

const INITIAL_FORM: FormState = {
  scriptText: '',
  targetFrames: '',
  style: '',
};

export function ScriptImportDialog({
  projectId,
  isOpen,
  onClose,
}: ScriptImportDialogProps) {
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [status, setStatus] = useState<Status>('idle');
  const [errorMessage, setErrorMessage] = useState('');

  // Reset state when dialog opens
  useEffect(() => {
    if (isOpen) {
      setForm(INITIAL_FORM);
      setStatus('idle');
      setErrorMessage('');
    }
  }, [isOpen]);

  // Escape key to close
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const updateField = useCallback(
    <K extends keyof FormState>(field: K, value: FormState[K]) => {
      setForm((prev) => ({ ...prev, [field]: value }));
    },
    [],
  );

  const handleSubmit = useCallback(async () => {
    const trimmedScript = form.scriptText.trim();
    if (!trimmedScript) return;

    setStatus('submitting');
    setErrorMessage('');

    try {
      const targetFrames = form.targetFrames
        ? parseInt(form.targetFrames, 10)
        : undefined;

      await splitScript({
        project_id: projectId,
        script_text: trimmedScript,
        target_frames:
          targetFrames && !isNaN(targetFrames) ? targetFrames : undefined,
        style: form.style || undefined,
      });

      setStatus('success');
      // Auto-close after short delay
      setTimeout(() => {
        onClose();
      }, 2000);
    } catch (err) {
      setStatus('error');
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to submit script.',
      );
    }
  }, [form, projectId, onClose]);

  if (!isOpen) return null;

  const isSubmitting = status === 'submitting';
  const canSubmit = form.scriptText.trim().length > 0 && !isSubmitting;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="relative w-full max-w-lg overflow-hidden rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-zinc-700/60 px-5 py-3">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-zinc-400" />
            <span className="text-sm font-medium text-zinc-100">
              Import Script
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-6 w-6 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-700 hover:text-zinc-100"
            title="Close (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="space-y-4 px-5 py-4">
          {/* Script textarea */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-300">
              Script Text
            </label>
            <textarea
              value={form.scriptText}
              onChange={(e) => updateField('scriptText', e.target.value)}
              placeholder="Paste your script here..."
              rows={8}
              className="w-full resize-y rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 outline-none transition-colors focus:border-zinc-500"
              disabled={isSubmitting}
            />
          </div>

          {/* Options row */}
          <div className="flex gap-3">
            {/* Target frames */}
            <div className="flex-1">
              <label className="mb-1.5 block text-xs font-medium text-zinc-300">
                Target Frames
                <span className="ml-1 text-zinc-500">(optional)</span>
              </label>
              <input
                type="number"
                min={1}
                max={100}
                value={form.targetFrames}
                onChange={(e) => updateField('targetFrames', e.target.value)}
                placeholder="Auto"
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 outline-none transition-colors focus:border-zinc-500"
                disabled={isSubmitting}
              />
            </div>

            {/* Style */}
            <div className="flex-1">
              <label className="mb-1.5 block text-xs font-medium text-zinc-300">
                Style
                <span className="ml-1 text-zinc-500">(optional)</span>
              </label>
              <select
                value={form.style}
                onChange={(e) => updateField('style', e.target.value)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none transition-colors focus:border-zinc-500"
                disabled={isSubmitting}
              >
                {STYLE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Status messages */}
          {status === 'success' && (
            <p className="rounded-lg bg-emerald-900/30 px-3 py-2 text-sm text-emerald-300">
              Script submitted for processing. Frames will appear on canvas when
              ready.
            </p>
          )}
          {status === 'error' && errorMessage && (
            <p className="rounded-lg bg-red-900/30 px-3 py-2 text-sm text-red-300">
              {errorMessage}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-zinc-700/60 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-zinc-400 transition-colors hover:text-zinc-100"
            disabled={isSubmitting}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {isSubmitting ? 'Processing...' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  );
}
