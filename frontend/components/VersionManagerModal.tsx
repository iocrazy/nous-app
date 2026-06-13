import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Layers, Loader2, Upload, Trash2, Check, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ResourceVersion } from '../types';
import {
  fetchResourceVersions,
  uploadNewVersion,
  setCurrentVersion,
  deleteVersion,
} from '../services/resourceService';

interface VersionManagerModalProps {
  isOpen: boolean;
  onClose: () => void;
  resourceId: string;
  currentVersionNumber: number;
  onVersionChange: () => void;
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
  resourceId,
  currentVersionNumber,
  onVersionChange,
}) => {
  const { t } = useTranslation();
  const [versions, setVersions] = useState<ResourceVersion[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ResourceVersion | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [settingCurrent, setSettingCurrent] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadVersions = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchResourceVersions(resourceId);
      setVersions(data.sort((a, b) => b.version_number - a.version_number));
    } catch (err: unknown) {
      const errorObj = err as { message?: string };
      setError(errorObj?.message || 'Failed to load versions');
    } finally {
      setIsLoading(false);
    }
  }, [resourceId]);

  useEffect(() => {
    if (isOpen) {
      loadVersions();
    }
  }, [isOpen, loadVersions]);

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    setError(null);
    try {
      await uploadNewVersion(resourceId, file);
      await loadVersions();
      onVersionChange();
    } catch (err: unknown) {
      const errorObj = err as { message?: string };
      setError(errorObj?.message || 'Failed to upload version');
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleSetCurrent = async (versionNumber: number) => {
    if (versionNumber === currentVersionNumber) return;
    setSettingCurrent(versionNumber);
    setError(null);
    try {
      await setCurrentVersion(resourceId, versionNumber);
      onVersionChange();
    } catch (err: unknown) {
      const errorObj = err as { message?: string };
      setError(errorObj?.message || 'Failed to set current version');
    } finally {
      setSettingCurrent(null);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!pendingDelete) return;
    setIsDeleting(true);
    setError(null);
    try {
      await deleteVersion(resourceId, pendingDelete.id);
      await loadVersions();
      onVersionChange();
    } catch (err: unknown) {
      const errorObj = err as { message?: string };
      setError(errorObj?.message || 'Failed to delete version');
    } finally {
      setIsDeleting(false);
      setPendingDelete(null);
    }
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
      <div className="relative bg-ink-900 border border-ink-800 rounded-2xl shadow-2xl w-full max-w-lg mx-4 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-ink-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/20 rounded-lg">
              <Layers size={20} className="text-indigo-400" />
            </div>
            <h2 className="text-lg font-semibold text-white">
              {t('resources.manageVersions', 'Manage Versions')}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-ink-400 hover:text-white hover:bg-ink-800 rounded-lg transition-colors"
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

          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 size={24} className="text-ink-400 animate-spin" />
            </div>
          ) : versions.length === 0 ? (
            <div className="text-center py-8 text-ink-500 text-sm">
              {t('resources.noVersions', 'No versions found')}
            </div>
          ) : (
            <div className="space-y-2">
              {versions.map((version) => {
                const isCurrent = version.version_number === currentVersionNumber;
                return (
                  <div
                    key={version.id}
                    className={`p-3 rounded-xl border transition-colors ${
                      isCurrent
                        ? 'border-indigo-500/30 bg-indigo-500/5'
                        : 'border-ink-800 hover:border-ink-700 hover:bg-ink-800/50'
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      {/* Version badge */}
                      <span
                        className={`px-2 py-0.5 text-xs font-semibold rounded-full ${
                          isCurrent
                            ? 'bg-indigo-500/20 text-indigo-300'
                            : 'bg-ink-700 text-ink-300'
                        }`}
                      >
                        V{version.version_number}
                      </span>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm text-white truncate">
                            {version.filename || 'Untitled'}
                          </span>
                          {isCurrent && (
                            <span className="px-1.5 py-0.5 text-[10px] font-medium bg-emerald-500/20 text-emerald-400 rounded">
                              {t('resources.currentLabel', 'Current')}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 mt-1 text-xs text-ink-500">
                          {version.created_at && (
                            <span>{formatDate(version.created_at)}</span>
                          )}
                          {version.file_size_bytes !== null && (
                            <>
                              <span className="text-ink-700">|</span>
                              <span>{formatFileSize(version.file_size_bytes)}</span>
                            </>
                          )}
                          {version.resolution && (
                            <>
                              <span className="text-ink-700">|</span>
                              <span>{version.resolution?.replace(/:/g, 'x')}</span>
                            </>
                          )}
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-1 shrink-0">
                        {!isCurrent && (
                          <button
                            onClick={() => handleSetCurrent(version.version_number)}
                            disabled={settingCurrent !== null}
                            className="p-1.5 text-ink-500 hover:text-emerald-400 hover:bg-emerald-500/10 rounded-lg transition-colors"
                            title={t('resources.setAsCurrent', 'Set as current')}
                          >
                            {settingCurrent === version.version_number ? (
                              <Loader2 size={14} className="animate-spin" />
                            ) : (
                              <Check size={14} />
                            )}
                          </button>
                        )}
                        {versions.length > 1 && (
                          <button
                            onClick={() => setPendingDelete(version)}
                            className="p-1.5 text-ink-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                            title={t('resources.deleteVersion', 'Delete version')}
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Upload Button */}
        <div className="p-6 border-t border-ink-800">
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
                <span>{t('resources.uploadingVersion', 'Uploading...')}</span>
              </>
            ) : (
              <>
                <Upload size={18} />
                <span>{t('resources.uploadNewVersion', 'Upload New Version')}</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Delete Confirmation Dialog */}
      {pendingDelete && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setPendingDelete(null)}
          />
          <div className="relative bg-ink-900 border border-ink-800 rounded-2xl shadow-2xl w-full max-w-sm mx-4">
            <div className="p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="p-2 bg-red-500/20 rounded-lg">
                  <AlertTriangle size={20} className="text-red-400" />
                </div>
                <h3 className="text-base font-semibold text-white">
                  {t('resources.confirmDeleteVersion', 'Delete Version')}
                </h3>
              </div>
              <p className="text-sm text-ink-400 mb-6">
                {t(
                  'resources.deleteVersionWarning',
                  'This will permanently delete V{{version}} and its files. This action cannot be undone.',
                ).replace('{{version}}', String(pendingDelete.version_number))}
              </p>
              <div className="flex gap-3">
                <button
                  onClick={() => setPendingDelete(null)}
                  className="flex-1 px-4 py-2 text-sm text-ink-300 bg-ink-800 hover:bg-ink-700 rounded-xl transition-colors"
                >
                  {t('common.cancel', 'Cancel')}
                </button>
                <button
                  onClick={handleDeleteConfirm}
                  disabled={isDeleting}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-red-600 hover:bg-red-500 disabled:bg-red-600/50 rounded-xl transition-colors"
                >
                  {isDeleting ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Trash2 size={14} />
                  )}
                  <span>{t('common.delete', 'Delete')}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
