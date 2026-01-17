import React, { useState, useEffect, useRef } from 'react';
import { X, Plus, Check, Users, Lock } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface Collection {
  id: string;
  name: string;
  isShared: boolean;
  videoCount: number;
}

interface CollectionPickerProps {
  isOpen: boolean;
  onClose: () => void;
  collections: Collection[];
  selectedIds: string[];
  onToggle: (collectionId: string) => void;
  onCreate: (name: string, isShared: boolean) => void;
}

export const CollectionPicker: React.FC<CollectionPickerProps> = ({
  isOpen,
  onClose,
  collections,
  selectedIds,
  onToggle,
  onCreate,
}) => {
  const { t } = useTranslation();
  const [isCreating, setIsCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newIsShared, setNewIsShared] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (isCreating && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isCreating]);

  if (!isOpen) return null;

  const handleCreate = () => {
    if (newName.trim()) {
      onCreate(newName.trim(), newIsShared);
      setNewName('');
      setNewIsShared(false);
      setIsCreating(false);
    }
  };

  return (
    <div
      ref={panelRef}
      className="absolute bottom-full right-0 mb-2 w-64 bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl z-50 animate-in fade-in slide-in-from-bottom-2 duration-200 overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <h3 className="font-medium text-zinc-200 text-sm">{t('collections.addTo')}</h3>
        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
          <X size={16} />
        </button>
      </div>

      {/* Collections List */}
      <div className="max-h-[240px] overflow-y-auto p-2 space-y-1">
        {collections.map(collection => (
          <button
            key={collection.id}
            onClick={() => onToggle(collection.id)}
            className="flex items-center justify-between w-full px-3 py-2 rounded-lg hover:bg-zinc-800/50 transition-colors group"
          >
            <div className="flex items-center gap-2 min-w-0">
              <div className={`w-5 h-5 rounded border flex items-center justify-center transition-colors ${
                selectedIds.includes(collection.id)
                  ? 'bg-indigo-500 border-indigo-500'
                  : 'border-zinc-600 group-hover:border-zinc-500'
              }`}>
                {selectedIds.includes(collection.id) && <Check size={12} className="text-white" />}
              </div>
              <span className="text-sm text-zinc-300 truncate">{collection.name}</span>
              {collection.isShared ? (
                <Users size={12} className="text-indigo-400 flex-shrink-0" />
              ) : (
                <Lock size={12} className="text-zinc-600 flex-shrink-0" />
              )}
            </div>
            <span className="text-xs text-zinc-600">{collection.videoCount}</span>
          </button>
        ))}
      </div>

      {/* Create New */}
      <div className="border-t border-zinc-800 p-2">
        {isCreating ? (
          <div className="space-y-2">
            <input
              ref={inputRef}
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              placeholder={t('team.name')}
              className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-200 placeholder-zinc-500 outline-none focus:border-indigo-500"
            />
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={newIsShared}
                  onChange={(e) => setNewIsShared(e.target.checked)}
                  className="rounded border-zinc-600"
                />
                <Users size={12} />
                {t('collections.shared')}
              </label>
              <div className="flex gap-2">
                <button
                  onClick={() => setIsCreating(false)}
                  className="px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleCreate}
                  disabled={!newName.trim()}
                  className="px-3 py-1 text-xs bg-indigo-600 text-white rounded-lg hover:bg-indigo-500 disabled:opacity-50"
                >
                  {t('common.save')}
                </button>
              </div>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setIsCreating(true)}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-indigo-400 hover:bg-indigo-500/10 transition-colors"
          >
            <Plus size={16} />
            <span className="text-sm font-medium">{t('collections.create')}</span>
          </button>
        )}
      </div>
    </div>
  );
};
