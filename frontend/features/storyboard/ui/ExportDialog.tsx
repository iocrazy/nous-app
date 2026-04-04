import { useState, useCallback, useEffect } from 'react';
import { X, Download, Loader2, Image, FileText, Archive } from 'lucide-react';
import {
  exportProject,
  ExportFormat,
  ExportProjectOptions,
} from '../../../services/storyboardService';

const FORMAT_TABS: Array<{
  value: ExportFormat;
  label: string;
  icon: typeof Image;
}> = [
  { value: 'png', label: 'PNG', icon: Image },
  { value: 'pdf', label: 'PDF', icon: FileText },
  { value: 'zip', label: 'ZIP', icon: Archive },
];

const QUALITY_OPTIONS: Array<{ value: 'low' | 'medium' | 'high'; label: string }> = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

const PAPER_SIZE_OPTIONS: Array<{ value: 'a4' | 'letter'; label: string }> = [
  { value: 'a4', label: 'A4' },
  { value: 'letter', label: 'Letter' },
];

interface ExportDialogProps {
  projectId: string;
  isOpen: boolean;
  onClose: () => void;
}

type Status = 'idle' | 'submitting' | 'success' | 'error';

interface FormState {
  format: ExportFormat;
  includeFrameNumbers: boolean;
  includeAnnotations: boolean;
  includeCameraOverlays: boolean;
  includeNotes: boolean;
  includeMetadata: boolean;
  includeCharacterPage: boolean;
  includeAllAssets: boolean;
  columns: number;
  paperSize: 'a4' | 'letter';
  quality: 'low' | 'medium' | 'high';
}

const INITIAL_FORM: FormState = {
  format: 'png',
  includeFrameNumbers: true,
  includeAnnotations: true,
  includeCameraOverlays: false,
  includeNotes: true,
  includeMetadata: false,
  includeCharacterPage: false,
  includeAllAssets: false,
  columns: 3,
  paperSize: 'a4',
  quality: 'medium',
};

function CheckboxRow({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled: boolean;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors hover:bg-zinc-800/60">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        className="h-3.5 w-3.5 rounded border-zinc-600 bg-zinc-800 text-blue-500 focus:ring-1 focus:ring-blue-500 focus:ring-offset-0"
      />
      <span className="text-sm text-zinc-300">{label}</span>
    </label>
  );
}

export function ExportDialog({
  projectId,
  isOpen,
  onClose,
}: ExportDialogProps) {
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

  const handleExport = useCallback(async () => {
    setStatus('submitting');
    setErrorMessage('');

    try {
      const options: ExportProjectOptions = {
        includeFrameNumbers: form.includeFrameNumbers,
        includeAnnotations: form.includeAnnotations,
        includeCameraOverlays: form.includeCameraOverlays,
        includeNotes: form.includeNotes,
        includeMetadata: form.includeMetadata,
        includeCharacterPage: form.includeCharacterPage,
        includeAllAssets: form.includeAllAssets,
        columns: form.columns,
        quality: form.quality,
        ...(form.format === 'pdf' ? { paperSize: form.paperSize } : {}),
      };

      await exportProject(projectId, form.format, options);
      setStatus('success');
      setTimeout(() => {
        onClose();
      }, 2500);
    } catch (err) {
      setStatus('error');
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to start export.',
      );
    }
  }, [form, projectId, onClose]);

  if (!isOpen) return null;

  const isSubmitting = status === 'submitting';

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="relative w-full max-w-md overflow-hidden rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-zinc-700/60 px-5 py-3">
          <div className="flex items-center gap-2">
            <Download className="h-4 w-4 text-zinc-400" />
            <span className="text-sm font-medium text-zinc-100">
              Export Storyboard
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
          {/* Format tabs */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-300">
              Format
            </label>
            <div className="flex gap-1 rounded-lg bg-zinc-800 p-1">
              {FORMAT_TABS.map((tab) => {
                const Icon = tab.icon;
                const isActive = form.format === tab.value;
                return (
                  <button
                    key={tab.value}
                    type="button"
                    onClick={() => updateField('format', tab.value)}
                    disabled={isSubmitting}
                    className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                      isActive
                        ? 'bg-zinc-600 text-zinc-100'
                        : 'text-zinc-400 hover:text-zinc-200'
                    }`}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Options checkboxes */}
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">
              Include
            </label>
            <div className="grid grid-cols-2 gap-x-2">
              <CheckboxRow
                label="Frame Numbers"
                checked={form.includeFrameNumbers}
                onChange={(v) => updateField('includeFrameNumbers', v)}
                disabled={isSubmitting}
              />
              <CheckboxRow
                label="Annotations"
                checked={form.includeAnnotations}
                onChange={(v) => updateField('includeAnnotations', v)}
                disabled={isSubmitting}
              />
              <CheckboxRow
                label="Camera Overlays"
                checked={form.includeCameraOverlays}
                onChange={(v) => updateField('includeCameraOverlays', v)}
                disabled={isSubmitting}
              />
              <CheckboxRow
                label="Notes"
                checked={form.includeNotes}
                onChange={(v) => updateField('includeNotes', v)}
                disabled={isSubmitting}
              />
              <CheckboxRow
                label="Metadata"
                checked={form.includeMetadata}
                onChange={(v) => updateField('includeMetadata', v)}
                disabled={isSubmitting}
              />
              <CheckboxRow
                label="Character Page"
                checked={form.includeCharacterPage}
                onChange={(v) => updateField('includeCharacterPage', v)}
                disabled={isSubmitting}
              />
              {form.format === 'zip' && (
                <CheckboxRow
                  label="All Assets"
                  checked={form.includeAllAssets}
                  onChange={(v) => updateField('includeAllAssets', v)}
                  disabled={isSubmitting}
                />
              )}
            </div>
          </div>

          {/* Layout & quality row */}
          <div className="flex gap-3">
            {/* Columns */}
            <div className="flex-1">
              <label className="mb-1.5 block text-xs font-medium text-zinc-300">
                Columns
              </label>
              <input
                type="number"
                min={1}
                max={6}
                value={form.columns}
                onChange={(e) => {
                  const val = parseInt(e.target.value, 10);
                  if (val >= 1 && val <= 6) {
                    updateField('columns', val);
                  }
                }}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none transition-colors focus:border-zinc-500"
                disabled={isSubmitting}
              />
            </div>

            {/* Quality */}
            <div className="flex-1">
              <label className="mb-1.5 block text-xs font-medium text-zinc-300">
                Quality
              </label>
              <select
                value={form.quality}
                onChange={(e) =>
                  updateField(
                    'quality',
                    e.target.value as 'low' | 'medium' | 'high',
                  )
                }
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none transition-colors focus:border-zinc-500"
                disabled={isSubmitting}
              >
                {QUALITY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Paper size (PDF only) */}
            {form.format === 'pdf' && (
              <div className="flex-1">
                <label className="mb-1.5 block text-xs font-medium text-zinc-300">
                  Paper Size
                </label>
                <select
                  value={form.paperSize}
                  onChange={(e) =>
                    updateField(
                      'paperSize',
                      e.target.value as 'a4' | 'letter',
                    )
                  }
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none transition-colors focus:border-zinc-500"
                  disabled={isSubmitting}
                >
                  {PAPER_SIZE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* Status messages */}
          {status === 'success' && (
            <p className="rounded-lg bg-emerald-900/30 px-3 py-2 text-sm text-emerald-300">
              Export started. You'll be notified when it's ready.
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
            onClick={handleExport}
            disabled={isSubmitting}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {isSubmitting ? 'Processing...' : 'Export'}
          </button>
        </div>
      </div>
    </div>
  );
}
