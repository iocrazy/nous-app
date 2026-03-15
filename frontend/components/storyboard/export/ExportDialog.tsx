import React, { useState, useCallback } from 'react';
import { X, Download, Loader2, CheckCircle } from 'lucide-react';
import ExportPreview from './ExportPreview';

// ─── Types ────────────────────────────────────────────────────────────────────

type ExportFormat = 'png' | 'pdf' | 'zip';

interface ExportOptions {
  format: ExportFormat;
  // PNG options
  columns: number;
  includeFrameNumbers: boolean;
  includeAnnotations: boolean;
  // PDF options
  includeMetadataTable: boolean;
  includeCharacterPage: boolean;
  // ZIP options
  includeAllAssets: boolean;
}

interface ExportDialogProps {
  projectId: string;
  frameCount?: number;
  onExport: (options: ExportOptions) => Promise<void>;
  onCancel: () => void;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const FORMAT_OPTIONS: { value: ExportFormat; label: string; description: string }[] = [
  { value: 'png', label: 'PNG Grid', description: 'Single image with all frames in a grid' },
  { value: 'pdf', label: 'PDF Document', description: 'Printable PDF with frame details' },
  { value: 'zip', label: 'ZIP Bundle', description: 'All assets in a compressed archive' },
];

// ─── Component ────────────────────────────────────────────────────────────────

const ExportDialog = React.memo(function ExportDialog({
  projectId: _projectId,
  frameCount = 0,
  onExport,
  onCancel,
}: ExportDialogProps) {
  const [options, setOptions] = useState<ExportOptions>({
    format: 'png',
    columns: 3,
    includeFrameNumbers: true,
    includeAnnotations: true,
    includeMetadataTable: true,
    includeCharacterPage: false,
    includeAllAssets: true,
  });

  const [status, setStatus] = useState<'idle' | 'exporting' | 'done'>('idle');

  const updateOption = useCallback(<K extends keyof ExportOptions>(key: K, value: ExportOptions[K]) => {
    setOptions((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleExport = useCallback(async () => {
    setStatus('exporting');
    try {
      await onExport(options);
      setStatus('done');
    } catch {
      setStatus('idle');
    }
  }, [options, onExport]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />

      <div className="relative bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-lg flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <div className="flex items-center gap-2">
            <Download size={16} className="text-blue-400" />
            <h2 className="text-sm font-semibold text-gray-100">Export Storyboard</h2>
          </div>
          <button onClick={onCancel} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* Format */}
          <div className="space-y-2">
            <p className="text-xs font-medium text-gray-400">Format</p>
            {FORMAT_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className={[
                  'flex items-center gap-3 p-3 rounded-xl border-2 cursor-pointer transition-all',
                  options.format === opt.value
                    ? 'border-blue-500 bg-blue-500/10'
                    : 'border-gray-700 hover:border-gray-600',
                ].join(' ')}
              >
                <input
                  type="radio"
                  name="format"
                  value={opt.value}
                  checked={options.format === opt.value}
                  onChange={() => updateOption('format', opt.value)}
                  className="accent-blue-500"
                />
                <div>
                  <p className="text-sm font-medium text-gray-200">{opt.label}</p>
                  <p className="text-xs text-gray-500">{opt.description}</p>
                </div>
              </label>
            ))}
          </div>

          {/* PNG Options */}
          {options.format === 'png' && (
            <div className="space-y-3">
              <p className="text-xs font-medium text-gray-400">PNG Options</p>
              <div className="flex items-center gap-3">
                <label className="text-xs text-gray-400 w-20">Columns</label>
                <input
                  type="number"
                  min={1}
                  max={8}
                  value={options.columns}
                  onChange={(e) => updateOption('columns', Number(e.target.value))}
                  className="w-16 px-2 py-1 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 text-center focus:outline-none focus:border-blue-500"
                />
              </div>
              <CheckboxOption
                label="Include frame numbers"
                checked={options.includeFrameNumbers}
                onChange={(v) => updateOption('includeFrameNumbers', v)}
              />
              <CheckboxOption
                label="Include annotations"
                checked={options.includeAnnotations}
                onChange={(v) => updateOption('includeAnnotations', v)}
              />
            </div>
          )}

          {/* PDF Options */}
          {options.format === 'pdf' && (
            <div className="space-y-3">
              <p className="text-xs font-medium text-gray-400">PDF Options</p>
              <CheckboxOption
                label="Include metadata table"
                checked={options.includeMetadataTable}
                onChange={(v) => updateOption('includeMetadataTable', v)}
              />
              <CheckboxOption
                label="Include character page"
                checked={options.includeCharacterPage}
                onChange={(v) => updateOption('includeCharacterPage', v)}
              />
            </div>
          )}

          {/* ZIP Options */}
          {options.format === 'zip' && (
            <div className="space-y-3">
              <p className="text-xs font-medium text-gray-400">ZIP Options</p>
              <CheckboxOption
                label="Include all assets"
                checked={options.includeAllAssets}
                onChange={(v) => updateOption('includeAllAssets', v)}
              />
            </div>
          )}

          {/* Preview */}
          <div>
            <p className="text-xs font-medium text-gray-400 mb-2">Preview</p>
            <ExportPreview
              format={options.format}
              columns={options.columns}
              includeFrameNumbers={options.includeFrameNumbers}
              frameCount={frameCount}
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-700">
          {status === 'done' && (
            <div className="flex items-center gap-1.5 text-green-400 text-sm mr-auto">
              <CheckCircle size={14} />
              Export started
            </div>
          )}
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors">
            {status === 'done' ? 'Close' : 'Cancel'}
          </button>
          {status !== 'done' && (
            <button
              onClick={handleExport}
              disabled={status === 'exporting' || frameCount === 0}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {status === 'exporting' ? (
                <><Loader2 size={14} className="animate-spin" /> Exporting...</>
              ) : (
                <><Download size={14} /> Export</>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
});

// ─── Sub-components ───────────────────────────────────────────────────────────

function CheckboxOption({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="w-3.5 h-3.5 accent-blue-500"
      />
      <span className="text-xs text-gray-300">{label}</span>
    </label>
  );
}

export default ExportDialog;
