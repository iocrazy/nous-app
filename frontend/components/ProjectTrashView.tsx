import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Trash2, RotateCcw, AlertTriangle } from 'lucide-react';
import { ProjectFile } from '../types';
import { fetchProjectFiles, updateFile, deleteFile } from '../services/projectsService';

interface ProjectTrashViewProps {
  projectId: string;
  onCountChange?: (count: number) => void;
}

export const ProjectTrashView: React.FC<ProjectTrashViewProps> = ({ projectId, onCountChange }) => {
  const { t } = useTranslation();
  const [trashedFiles, setTrashedFiles] = useState<ProjectFile[]>([]);
  const [loading, setLoading] = useState(true);

  const loadTrashed = async () => {
    try {
      setLoading(true);
      const files = await fetchProjectFiles(projectId, true);
      const trashed = files.filter(f => f.is_trashed);
      setTrashedFiles(trashed);
      onCountChange?.(trashed.length);
    } catch (err) {
      console.error('Failed to load trashed files:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadTrashed(); }, [projectId]);

  const handleRestore = async (file: ProjectFile) => {
    try {
      await updateFile(projectId, file.id, { is_trashed: false });
      loadTrashed();
    } catch (err) {
      console.error('Failed to restore file:', err);
    }
  };

  const handlePermanentDelete = async (file: ProjectFile) => {
    if (!window.confirm(t('projects.trash.confirmPermanentDelete',
      `Permanently delete "${file.filename}"? This cannot be undone.`))) return;
    try {
      await deleteFile(projectId, file.id);
      loadTrashed();
    } catch (err) {
      console.error('Failed to permanently delete file:', err);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleDateString();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500" />
      </div>
    );
  }

  if (trashedFiles.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <div className="p-4 bg-ink-800 rounded-2xl mb-4">
          <Trash2 size={40} className="text-ink-500" />
        </div>
        <h3 className="text-lg font-medium text-ink-300 mb-2">
          {t('projects.trash.empty', 'Trash is empty')}
        </h3>
        <p className="text-sm text-ink-500">
          {t('projects.trash.emptyHint', 'Deleted files will appear here')}
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <AlertTriangle size={14} className="text-amber-400" />
        <span className="text-xs text-ink-500">
          {t('projects.trash.hint', 'Files in trash can be restored or permanently deleted')}
        </span>
      </div>

      <div className="bg-ink-800/50 border border-ink-700/50 rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-ink-700/50">
              <th className="text-left px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">
                {t('projects.trash.fileName', 'File Name')}
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">
                {t('projects.trash.type', 'Type')}
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">
                {t('projects.trash.deletedAt', 'Deleted')}
              </th>
              <th className="text-right px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">
                {t('projects.trash.actions', 'Actions')}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-700/30">
            {trashedFiles.map(file => (
              <tr key={file.id} className="hover:bg-ink-700/20 transition-colors">
                <td className="px-4 py-3">
                  <span className="text-sm text-ink-50">{file.filename}</span>
                </td>
                <td className="px-4 py-3">
                  <span className="text-xs text-ink-400 bg-ink-700/50 px-2 py-0.5 rounded-full">
                    {file.file_type || file.mime_type || '—'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className="text-sm text-ink-500">{formatDate(file.trashed_at)}</span>
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-2">
                    <button
                      onClick={() => handleRestore(file)}
                      className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 px-2 py-1 hover:bg-indigo-500/10 rounded-lg transition-colors"
                    >
                      <RotateCcw size={12} />
                      {t('projects.trash.restore', 'Restore')}
                    </button>
                    <button
                      onClick={() => handlePermanentDelete(file)}
                      className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300 px-2 py-1 hover:bg-red-500/10 rounded-lg transition-colors"
                    >
                      <Trash2 size={12} />
                      {t('projects.trash.deletePermanently', 'Delete')}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
