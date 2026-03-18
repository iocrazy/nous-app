import React, { useState, useCallback } from 'react';
import { X, Download, Loader2, CheckCircle } from 'lucide-react';
import ExportPreview from './ExportPreview';

// ─── Types ────────────────────────────────────────────────────────────────────

type ExportFormat = 'png' | 'pdf' | 'zip';
type PaperSize = 'a4' | 'letter' | 'custom';
type ImageQuality = 'low' | 'medium' | 'high';

export interface ExportOptions {
  format: ExportFormat;
  // Overlay toggles
  includeFrameNumbers: boolean;
  includeAnnotations: boolean;
  includeCameraOverlays: boolean;
  includeNotes: boolean;
  includeMetadata: boolean;
  // Layout
  columns: number;
  // PDF-specific
  paperSize: PaperSize;
  includeCharacterPage: boolean;
  // Quality
  quality: ImageQuality;
  // ZIP-specific
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

const PAPER_SIZES: { value: PaperSize; label: string }[] = [
  { value: 'a4', label: 'A4' },
  { value: 'letter', label: 'Letter' },
  { value: 'custom', label: 'Custom' },
];

const QUALITY_OPTIONS: { value: ImageQuality; label: string }[] = [
  { value: 'low', label: 'Low (72 DPI)' },
  { value: 'medium', label: 'Medium (150 DPI)' },
  { value: 'high', label: 'High (300 DPI)' },
];

const COLUMN_OPTIONS = [1, 2, 3, 4, 5, 6];

// ─── Component ────────────────────────────────────────────────────────────────

const ExportDialog = React.memo(function ExportDialog({
  projectId: _projectId,
  frameCount = 0,
  onExport,
  onCancel,
}: ExportDialogProps) {
  const [options, setOptions] = useState<ExportOptions>({
    format: 'png',
    includeFrameNumbers: true,
    includeAnnotations: true,
    includeCameraOverlays: true,
    includeNotes: true,
    includeMetadata: true,
    columns: 3,
    paperSize: 'a4',
    includeCharacterPage: false,
    quality: 'medium',
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
          <Section title="Format">
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
          </Section>

          {/* Overlay Toggles (shared) */}
          <Section title="Content">
            <ToggleSwitch label="Frame numbers" checked={options.includeFrameNumbers} onChange={(v) => updateOption('includeFrameNumbers', v)} />
            <ToggleSwitch label="Annotations" checked={options.includeAnnotations} onChange={(v) => updateOption('includeAnnotations', v)} />
            <ToggleSwitch label="Camera overlays" checked={options.includeCameraOverlays} onChange={(v) => updateOption('includeCameraOverlays', v)} />
            <ToggleSwitch label="Notes / descriptions" checked={options.includeNotes} onChange={(v) => updateOption('includeNotes', v)} />
            <ToggleSwitch label="Embed metadata" checked={options.includeMetadata} onChange={(v) => updateOption('includeMetadata', v)} />
          </Section>

          {/* Layout — PNG & PDF */}
          {(options.format === 'png' || options.format === 'pdf') && (
            <Section title="Layout">
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-400 w-20">Columns</span>
                <div className="flex gap-1">
                  {COLUMN_OPTIONS.map((n) => (
                    <button
                      key={n}
                      onClick={() => updateOption('columns', n)}
                      className={[
                        'w-8 h-8 rounded-lg text-xs font-medium transition-colors',
                        options.columns === n
                          ? 'bg-blue-600 text-white'
                          : 'bg-gray-800 text-gray-400 hover:bg-gray-700',
                      ].join(' ')}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              </div>
            </Section>
          )}

          {/* PDF-specific */}
          {options.format === 'pdf' && (
            <Section title="PDF Options">
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-400 w-20">Paper size</span>
                <select
                  value={options.paperSize}
                  onChange={(e) => updateOption('paperSize', e.target.value as PaperSize)}
                  className="px-2 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                >
                  {PAPER_SIZES.map((ps) => (
                    <option key={ps.value} value={ps.value}>{ps.label}</option>
                  ))}
                </select>
              </div>
              <ToggleSwitch label="Include character page" checked={options.includeCharacterPage} onChange={(v) => updateOption('includeCharacterPage', v)} />
            </Section>
          )}

          {/* Quality — PNG & PDF */}
          {(options.format === 'png' || options.format === 'pdf') && (
            <Section title="Quality">
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-400 w-20">Output</span>
                <select
                  value={options.quality}
                  onChange={(e) => updateOption('quality', e.target.value as ImageQuality)}
                  className="px-2 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 focus:outline-none focus:border-blue-500"
                >
                  {QUALITY_OPTIONS.map((q) => (
                    <option key={q.value} value={q.value}>{q.label}</option>
                  ))}
                </select>
              </div>
            </Section>
          )}

          {/* ZIP-specific */}
          {options.format === 'zip' && (
            <Section title="ZIP Options">
              <ToggleSwitch label="Include all assets" checked={options.includeAllAssets} onChange={(v) => updateOption('includeAllAssets', v)} />
            </Section>
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

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-gray-400">{title}</p>
      {children}
    </div>
  );
}

function ToggleSwitch({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center justify-between gap-2 cursor-pointer py-0.5">
      <span className="text-xs text-gray-300">{label}</span>
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={['relative w-9 h-5 rounded-full transition-colors', checked ? 'bg-blue-600' : 'bg-gray-700'].join(' ')}>
        <span className={['absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform', checked ? 'translate-x-4' : 'translate-x-0'].join(' ')} />
      </button>
    </label>
  );
}

export default ExportDialog;
