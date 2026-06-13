import React, { useState } from 'react';
import { AlertTriangle, X, FileCheck, Copy } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { formatFileSize } from '../contexts/UploadContext';

interface ExistingResource {
  id: string;
  filename: string;
  file_size_bytes: number;
  thumbnail_path?: string;
  cover_image_path?: string;
  created_at: string;
}

interface DuplicateFileAlertProps {
  file: File;
  existing: ExistingResource;
  onUseExisting: (applyToAll: boolean) => void;
  onKeepBoth: (applyToAll: boolean) => void;
  onCancel: () => void;
  remainingDuplicates?: number;
}

export const DuplicateFileAlert: React.FC<DuplicateFileAlertProps> = ({
  file,
  existing,
  onUseExisting,
  onKeepBoth,
  onCancel,
  remainingDuplicates = 0,
}) => {
  const { t } = useTranslation();
  const [applyToAll, setApplyToAll] = useState(false);

  const apiUrl = import.meta.env.VITE_API_URL || '';
  const coverSrc = existing.id
    ? `${apiUrl}/api/v1/resources/${existing.id}/cover`
    : undefined;

  const formattedDate = new Date(existing.created_at).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onCancel}
      />
      <div className="relative bg-ink-900 border border-ink-800 rounded-2xl shadow-2xl w-full max-w-md mx-4 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-ink-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-amber-500/20 rounded-lg">
              <AlertTriangle size={20} className="text-amber-400" />
            </div>
            <h2 className="text-lg font-semibold text-ink-50">
              {t('resources.duplicateDetected')}
            </h2>
          </div>
          <button
            onClick={onCancel}
            className="p-2 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="p-5 space-y-4">
          <p className="text-sm text-ink-300">
            {t('resources.duplicateMessage', { filename: file.name })}
          </p>

          {/* Existing file card */}
          <div className="flex items-center gap-4 p-3 bg-ink-800/60 rounded-xl border border-ink-700/50">
            {coverSrc ? (
              <img
                src={coverSrc}
                alt={existing.filename}
                className="w-16 h-16 rounded-lg object-cover bg-ink-700 flex-shrink-0"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none';
                }}
              />
            ) : (
              <div className="w-16 h-16 rounded-lg bg-ink-700 flex items-center justify-center flex-shrink-0">
                <FileCheck size={24} className="text-ink-500" />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink-50 truncate">
                {existing.filename}
              </p>
              <p className="text-xs text-ink-400 mt-1">
                {formatFileSize(existing.file_size_bytes)}
                <span className="mx-1.5 text-ink-600">&middot;</span>
                {formattedDate}
              </p>
            </div>
          </div>

          {/* Apply to all checkbox */}
          {remainingDuplicates > 0 && (
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={applyToAll}
                onChange={(e) => setApplyToAll(e.target.checked)}
                className="w-4 h-4 rounded border-ink-600 bg-ink-800 text-indigo-500 focus:ring-indigo-500/30 focus:ring-offset-0"
              />
              <span className="text-sm text-ink-400">
                {t('resources.applyToAll')}
              </span>
            </label>
          )}
        </div>

        {/* Footer */}
        <div className="flex gap-3 p-5 border-t border-ink-800">
          <button
            type="button"
            onClick={() => onUseExisting(applyToAll)}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-3 text-white bg-indigo-600 hover:bg-indigo-500 rounded-xl font-medium transition-colors"
          >
            <FileCheck size={16} />
            {t('resources.useExisting')}
          </button>
          <button
            type="button"
            onClick={() => onKeepBoth(applyToAll)}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-3 text-ink-300 bg-ink-800 hover:bg-ink-700 rounded-xl font-medium transition-colors"
          >
            <Copy size={16} />
            {t('resources.keepBoth')}
          </button>
        </div>
      </div>
    </div>
  );
};
