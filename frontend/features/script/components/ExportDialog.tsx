import React, { useState } from 'react';
import {
  Download,
  X,
  GitBranch,
  FileText,
  Braces,
  FileCode,
  ChevronRight,
  Check,
} from 'lucide-react';
import { getExportUrl } from '../../../services/scriptService';

interface Branch {
  id: string;
  title: string;
}

interface ExportDialogProps {
  open: boolean;
  onClose: () => void;
  scriptId: string;
  hasBranches: boolean;
  branches?: Branch[];
}

type Step = 'branch' | 'format';

interface ExportFormat {
  key: string;
  label: string;
  description: string;
  icon: React.ReactNode;
  iconColor: string;
}

const EXPORT_FORMATS: ExportFormat[] = [
  {
    key: 'txt',
    label: 'Export as TXT',
    description: 'Plain text, easy to share',
    icon: <FileText className="w-5 h-5" />,
    iconColor: 'text-zinc-300',
  },
  {
    key: 'docx',
    label: 'Export as Word (.docx)',
    description: 'Microsoft Word document',
    icon: (
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm1 1.5 4.5 4.5H15V3.5zM6 20V4h7v6h6v10H6z" />
      </svg>
    ),
    iconColor: 'text-blue-400',
  },
  {
    key: 'json',
    label: 'Export as JSON',
    description: 'Structured data for developers',
    icon: <Braces className="w-5 h-5" />,
    iconColor: 'text-yellow-400',
  },
  {
    key: 'md',
    label: 'Export as Markdown',
    description: 'Markdown-formatted text',
    icon: <FileCode className="w-5 h-5" />,
    iconColor: 'text-purple-400',
  },
];

export function ExportDialog({
  open,
  onClose,
  scriptId,
  hasBranches,
  branches = [],
}: ExportDialogProps) {
  const initialStep: Step = hasBranches && branches.length > 0 ? 'branch' : 'format';
  const [step, setStep] = useState<Step>(initialStep);
  const [selectedBranchId, setSelectedBranchId] = useState<string>('');

  const handleClose = () => {
    setStep(initialStep);
    setSelectedBranchId('');
    onClose();
  };

  const handleExport = (format: string) => {
    const url = getExportUrl(scriptId, format, selectedBranchId || undefined);
    window.open(url, '_blank', 'noopener,noreferrer');
    handleClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={handleClose} />

      {/* Dialog */}
      <div className="relative z-10 w-full max-w-md mx-4 bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-zinc-700 shrink-0">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-green-600/20">
            <Download className="w-4 h-4 text-green-400" />
          </div>
          <h2 className="flex-1 text-base font-semibold text-zinc-100">
            {step === 'branch' ? 'Select Export Branch' : 'Export Script'}
          </h2>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700 transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4">
          {step === 'branch' ? (
            <BranchStep
              branches={branches}
              selectedBranchId={selectedBranchId}
              onSelect={setSelectedBranchId}
              onNext={() => setStep('format')}
            />
          ) : (
            <FormatStep
              onExport={handleExport}
              onBack={hasBranches && branches.length > 0 ? () => setStep('branch') : undefined}
              selectedBranchTitle={
                selectedBranchId
                  ? branches.find((b) => b.id === selectedBranchId)?.title
                  : undefined
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Branch Step ──────────────────────────────────────────────────────────────

interface BranchStepProps {
  branches: Branch[];
  selectedBranchId: string;
  onSelect: (id: string) => void;
  onNext: () => void;
}

function BranchStep({ branches, selectedBranchId, onSelect, onNext }: BranchStepProps) {
  if (branches.length === 0) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-zinc-800 border border-zinc-700">
          <GitBranch className="w-4 h-4 text-zinc-400 shrink-0" />
          <p className="text-sm text-zinc-400">No branches — will export complete story</p>
        </div>
        <div className="flex justify-end">
          <button
            onClick={onNext}
            className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-500 transition-colors"
          >
            Select Format
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-400">
        Choose a story branch to export, or export the complete story.
      </p>

      {/* Branch list */}
      <div className="space-y-1.5 max-h-60 overflow-y-auto pr-0.5">
        {/* "Complete story" option */}
        <BranchOption
          id=""
          label="Complete Story"
          description="Export all chapters"
          selected={selectedBranchId === ''}
          onSelect={() => onSelect('')}
          icon={<FileText className="w-4 h-4 text-zinc-400" />}
        />
        {branches.map((branch) => (
          <BranchOption
            key={branch.id}
            id={branch.id}
            label={branch.title}
            selected={selectedBranchId === branch.id}
            onSelect={() => onSelect(branch.id)}
            icon={<GitBranch className="w-4 h-4 text-blue-400" />}
          />
        ))}
      </div>

      <div className="flex justify-end">
        <button
          onClick={onNext}
          className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-500 transition-colors"
        >
          Select Format
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

interface BranchOptionProps {
  id: string;
  label: string;
  description?: string;
  selected: boolean;
  onSelect: () => void;
  icon: React.ReactNode;
}

function BranchOption({ label, description, selected, onSelect, icon }: BranchOptionProps) {
  return (
    <button
      onClick={onSelect}
      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg border text-left transition-colors ${
        selected
          ? 'border-blue-500 bg-blue-500/10'
          : 'border-zinc-700 bg-zinc-800 hover:border-zinc-600 hover:bg-zinc-750'
      }`}
    >
      <span className="shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <p
          className={`text-sm font-medium truncate ${selected ? 'text-blue-200' : 'text-zinc-200'}`}
        >
          {label}
        </p>
        {description && <p className="text-xs text-zinc-500 truncate">{description}</p>}
      </div>
      {selected && <Check className="w-4 h-4 text-blue-400 shrink-0" />}
    </button>
  );
}

// ─── Format Step ─────────────────────────────────────────────────────────────

interface FormatStepProps {
  onExport: (format: string) => void;
  onBack?: () => void;
  selectedBranchTitle?: string;
}

function FormatStep({ onExport, onBack, selectedBranchTitle }: FormatStepProps) {
  return (
    <div className="space-y-3">
      {selectedBranchTitle && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700">
          <GitBranch className="w-3.5 h-3.5 text-blue-400 shrink-0" />
          <p className="text-xs text-zinc-400">
            Branch: <span className="text-zinc-200 font-medium">{selectedBranchTitle}</span>
          </p>
        </div>
      )}

      <p className="text-sm text-zinc-400">Choose an export format:</p>

      <div className="space-y-1.5">
        {EXPORT_FORMATS.map((fmt) => (
          <button
            key={fmt.key}
            onClick={() => onExport(fmt.key)}
            className="w-full flex items-center gap-3 px-3 py-3 rounded-lg border border-zinc-700 bg-zinc-800 hover:border-zinc-500 hover:bg-zinc-750 transition-colors text-left group"
          >
            <span className={`shrink-0 ${fmt.iconColor}`}>{fmt.icon}</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-zinc-200 group-hover:text-zinc-100 transition-colors">
                {fmt.label}
              </p>
              <p className="text-xs text-zinc-500">{fmt.description}</p>
            </div>
            <ChevronRight className="w-4 h-4 text-zinc-600 group-hover:text-zinc-400 transition-colors shrink-0" />
          </button>
        ))}
      </div>

      {onBack && (
        <div className="pt-1">
          <button
            onClick={onBack}
            className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            ← Back
          </button>
        </div>
      )}
    </div>
  );
}
