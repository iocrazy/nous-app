import React, { useState, useEffect, useRef } from 'react';
import { X, Layers, Loader2, Upload } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { FileVersion } from '../types';
import { fetchFileVersions, uploadNewVersion } from '../services/projectsService';

interface VersionManagerModalProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
  fileId: string;
  currentVersionNumber: number;
  onVersionSelect: (version: FileVersion) => void;
  onVersionUploaded: () => void;
}

const formatFileSize = (bytes: number | null): string => {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const index = Math.min(i, units.length - 1);
  return `${(bytes / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
};

const formatDate = (dateString: string): string => {
  try {
    return new Date(dateString).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return dateString;
  }
};

export const VersionManagerModal: React.FC<VersionManagerModalProps> = ({
  isOpen,
  onClose,
  projectId,
  fileId,
  currentVersionNumber,
  onVersionSelect,
  onVersionUploaded,
}) => {
  const { t } = useTranslation();
  const [versions, setVersions] = useState<FileVersion[]>([]);
  const [isLoadingVersions, setIsLoadingVersions] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      loadVersions();
    }
  }, [isOpen, projectId, fileId]);

  const loadVersions = async () => {
    setIsLoadingVersions(true);
    setError(null);
    try {
      const data = await fetchFileVersions(projectId, fileId);
      setVersions(data);
    } catch (err: unknown) {
      console.error('Failed to load versions:', err);
      const errorObj = err as { message?: string };
      setError(errorObj?.message || 'Failed to load versions');
    } finally {
      setIsLoadingVersions(false);
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    setError(null);
    try {
      await uploadNewVersion(projectId, fileId, file);
      await loadVersions();
      onVersionUploaded();
    } catch (err: unknown) {
      console.error('Failed to upload version:', err);
      const errorObj = err as { message?: string };
      setError(errorObj?.message || 'Failed to upload version');
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleVersionClick = (version: FileVersion) => {
    onVersionSelect(version);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-lg mx-4 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-zinc-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/20 rounded-lg">
              <Layers size={20} className="text-indigo-400" />
            </div>
            <h2 className="text-lg font-semibold text-white">
              {t('mediatrack.review.manageVersions') || 'Manage Versions'}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Version List */}
        <div className="p-6 max-h-96 overflow-y-auto">
          {error && (
            <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          {isLoadingVersions ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 size={24} className="text-zinc-400 animate-spin" />
            </div>
          ) : versions.length === 0 ? (
            <div className="text-center py-8 text-zinc-500 text-sm">
              No versions found
            </div>
          ) : (
            <div className="space-y-2">
              {versions.map((version) => {
                const isCurrent = version.version_number === currentVersionNumber;
                return (
                  <button
                    key={version.id}
                    onClick={() => handleVersionClick(version)}
                    className="w-full text-left p-3 rounded-xl border border-zinc-800 hover:border-zinc-700 hover:bg-zinc-800/50 transition-colors group"
                  >
                    <div className="flex items-center gap-3">
                      {/* Version badge */}
                      <span
                        className={`px-2 py-0.5 text-xs font-semibold rounded-full ${
                          isCurrent
                            ? 'bg-indigo-500/20 text-indigo-300'
                            : 'bg-zinc-700 text-zinc-300'
                        }`}
                      >
                        V{version.version_number}
                      </span>

                      {/* Filename & current label */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm text-white truncate">
                            {version.filename || 'Untitled'}
                          </span>
                          {isCurrent && (
                            <span className="px-1.5 py-0.5 text-[10px] font-medium bg-emerald-500/20 text-emerald-400 rounded">
                              {t('mediatrack.review.currentVersion') || 'Current'}
                            </span>
                          )}
                        </div>

                        {/* Metadata */}
                        <div className="flex items-center gap-2 mt-1 text-xs text-zinc-500">
                          {version.uploaded_by && (
                            <span>{version.uploaded_by}</span>
                          )}
                          {version.uploaded_by && version.created_at && (
                            <span className="text-zinc-700">|</span>
                          )}
                          {version.created_at && (
                            <span>{formatDate(version.created_at)}</span>
                          )}
                          {version.file_size_bytes !== null && (
                            <>
                              <span className="text-zinc-700">|</span>
                              <span>{formatFileSize(version.file_size_bytes)}</span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Upload Button */}
        <div className="p-6 border-t border-zinc-800">
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={handleFileChange}
          />
          <button
            onClick={handleUploadClick}
            disabled={isUploading}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 text-white bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 disabled:cursor-not-allowed rounded-xl font-medium transition-colors"
          >
            {isUploading ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                <span>Uploading...</span>
              </>
            ) : (
              <>
                <Upload size={18} />
                <span>{t('mediatrack.review.uploadNewVersion') || 'Upload New Version'}</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
