import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Copy, Check, Inbox, Trash2, Calendar, FileType } from 'lucide-react';
import { createProjectCollection, fetchProjectCollections, deleteProjectCollection } from '../services/projectsService';

interface ProjectCollectModalProps {
  projectId: string;
  isOpen: boolean;
  onClose: () => void;
}

export const ProjectCollectModal: React.FC<ProjectCollectModalProps> = ({
  projectId, isOpen, onClose,
}) => {
  const { t } = useTranslation();
  const [collections, setCollections] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [deadline, setDeadline] = useState('');
  const [maxSize, setMaxSize] = useState(500);
  const [isCreating, setIsCreating] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) loadCollections();
  }, [isOpen, projectId]);

  const loadCollections = async () => {
    setIsLoading(true);
    try {
      const data = await fetchProjectCollections(projectId);
      setCollections(data);
    } catch { /* silent */ }
    setIsLoading(false);
  };

  const handleCreate = async () => {
    if (!name.trim()) return;
    setIsCreating(true);
    try {
      await createProjectCollection(projectId, {
        collection_name: name.trim(),
        max_file_size_mb: maxSize,
        deadline: deadline || undefined,
      });
      setName('');
      setDeadline('');
      setShowCreate(false);
      await loadCollections();
    } catch { /* silent */ }
    setIsCreating(false);
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteProjectCollection(projectId, id);
      await loadCollections();
    } catch { /* silent */ }
  };

  const handleCopy = (code: string, id: string) => {
    const url = `${window.location.origin}/collect/${code}`;
    navigator.clipboard.writeText(url);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-ink-900 border border-ink-700 rounded-2xl w-full max-w-lg mx-4 overflow-hidden shadow-2xl max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-ink-800 shrink-0">
          <h3 className="text-base font-medium text-ink-100 flex items-center gap-2">
            <Inbox size={18} className="text-indigo-400" />
            {t('projects.collect.title', 'File Collection')}
          </h3>
          <button onClick={onClose} className="p-1 text-ink-500 hover:text-ink-300 transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Existing collections */}
          {isLoading ? (
            <p className="text-sm text-ink-500 text-center py-4">Loading...</p>
          ) : collections.length === 0 && !showCreate ? (
            <p className="text-sm text-ink-500 text-center py-4">
              {t('projects.collect.empty', 'No collection links yet')}
            </p>
          ) : (
            collections.map((c) => (
              <div key={c.id} className="bg-ink-800/50 border border-ink-700/50 rounded-xl px-4 py-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-ink-200">{c.collection_name}</span>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => handleCopy(c.collection_code, c.id)}
                      className="p-1.5 text-ink-500 hover:text-ink-300 transition-colors"
                      title="Copy link"
                    >
                      {copiedId === c.id ? <Check size={14} className="text-green-400" /> : <Copy size={14} />}
                    </button>
                    <button
                      onClick={() => handleDelete(c.id)}
                      className="p-1.5 text-ink-500 hover:text-red-400 transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-3 text-xs text-ink-500">
                  <span className={`px-1.5 py-0.5 rounded ${c.is_active ? 'bg-green-500/10 text-green-400' : 'bg-ink-700 text-ink-500'}`}>
                    {c.is_active ? 'Active' : 'Inactive'}
                  </span>
                  {c.deadline && (
                    <span className="flex items-center gap-1">
                      <Calendar size={10} />
                      {new Date(c.deadline).toLocaleDateString()}
                    </span>
                  )}
                  <span>{c.max_file_size_mb} MB max</span>
                </div>
              </div>
            ))
          )}

          {/* Create form */}
          {showCreate ? (
            <div className="border border-ink-700 rounded-xl p-4 space-y-3">
              <input
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('projects.collect.namePlaceholder', 'Collection name')}
                className="w-full bg-ink-800 border border-ink-700 rounded-lg px-3 py-2 text-sm text-ink-200 placeholder-ink-600 focus:outline-none focus:border-indigo-500"
              />
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-ink-500 mb-1 block flex items-center gap-1">
                    <Calendar size={10} />
                    {t('projects.collect.deadline', 'Deadline')}
                  </label>
                  <input
                    type="date"
                    value={deadline}
                    onChange={(e) => setDeadline(e.target.value)}
                    className="w-full bg-ink-800 border border-ink-700 rounded-lg px-3 py-2 text-sm text-ink-200 focus:outline-none focus:border-indigo-500"
                  />
                </div>
                <div>
                  <label className="text-xs text-ink-500 mb-1 block flex items-center gap-1">
                    <FileType size={10} />
                    {t('projects.collect.maxSize', 'Max File Size (MB)')}
                  </label>
                  <input
                    type="number"
                    value={maxSize}
                    onChange={(e) => setMaxSize(Number(e.target.value))}
                    min={1}
                    max={2000}
                    className="w-full bg-ink-800 border border-ink-700 rounded-lg px-3 py-2 text-sm text-ink-200 focus:outline-none focus:border-indigo-500"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setShowCreate(false)}
                  className="px-3 py-1.5 text-sm text-ink-400 hover:text-ink-200 transition-colors"
                >
                  {t('common.cancel', 'Cancel')}
                </button>
                <button
                  onClick={handleCreate}
                  disabled={!name.trim() || isCreating}
                  className="px-4 py-1.5 text-sm bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-lg transition-colors"
                >
                  {isCreating
                    ? t('projects.collect.creating', 'Creating...')
                    : t('projects.collect.create', 'Create')}
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setShowCreate(true)}
              className="w-full py-2.5 border border-dashed border-ink-700 hover:border-indigo-500/50 rounded-xl text-sm text-ink-400 hover:text-indigo-300 transition-colors"
            >
              + {t('projects.collect.newCollection', 'New Collection Link')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
